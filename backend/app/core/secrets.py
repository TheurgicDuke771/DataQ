"""Secret resolution abstraction.

Three backends are supported, picked from `settings.secret_store`:

- **EnvSecretStore** — reads secrets from env vars prefixed `KV_SECRET_`.
  Local dev only — convenient when running against `docker-compose` without
  an Azure tenant. Name normalisation: `snowflake-uat-finance` →
  env var `KV_SECRET_SNOWFLAKE_UAT_FINANCE`. **Per-process**: a secret written
  via `set` is only visible to the writing process (#86).

- **RedisSecretStore** — reads/writes secrets in Redis (already in the dev
  stack). **Dev/test only** and **plaintext** — but, unlike EnvSecretStore, a
  secret `set` by the API process is visible to the Celery worker, so
  connection-driven worker runs can resolve a credential the API just wrote
  (#86). Not for production (no encryption) — production uses Key Vault.

- **AzureKeyVaultStore** — reads from Azure Key Vault via
  `azure-identity` (DefaultAzureCredential) + `azure-keyvault-secrets`.
  Production / staging. Real vault provisioning + tenant config land in
  Week 7 (deployment hardening); the code path is wired now so callers
  can take a dependency on `SecretStore` without waiting.

The Azure SDK and the redis client are **lazy-imported** so deployments that
don't use them don't pay the import cost.
"""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

import redis

from backend.app.core.config import Settings, get_settings
from backend.app.core.logging import get_logger

if TYPE_CHECKING:
    from azure.keyvault.secrets import SecretClient

log = get_logger(__name__)

ENV_PREFIX: Final = "KV_SECRET_"
_AKV_MODE: Final = "azure_key_vault"
_REDIS_MODE: Final = "redis"
# Namespace for secret keys in the shared dev Redis (keeps them clear of Celery's
# own keys on the same instance).
_REDIS_KEY_PREFIX: Final = "dataq:secret:"


class SecretNotFoundError(Exception):
    """Raised when the requested secret is missing or unreadable."""


class SecretWriteError(Exception):
    """Raised when a secret cannot be written to the backing store."""


@runtime_checkable
class SecretStore(Protocol):
    def get(self, name: str) -> str: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None:
        """Best-effort removal of a secret (#372). Idempotent — a missing secret is a
        clean no-op — and **fail-soft**: it never raises, since it only ever runs as
        cleanup when the owning entity (connection / suite notification) is deleted or
        its secret cleared, and that must not 500 on a store hiccup. Failures are
        logged."""
        ...


def _env_key(name: str) -> str:
    return f"{ENV_PREFIX}{name.upper().replace('-', '_')}"


class EnvSecretStore:
    """Resolves secrets from `KV_SECRET_*` env vars. Local dev only."""

    def get(self, name: str) -> str:
        key = _env_key(name)
        value = os.environ.get(key)
        if value is None:
            raise SecretNotFoundError(f"Env secret {key!r} not set (mapped from name={name!r})")
        return value

    def set(self, name: str, value: str) -> None:
        """Write into the process env. Dev only — NOT persisted across restarts.

        Lets connection-CRUD exercise the write-through path locally without an
        Azure tenant. Production uses AzureKeyVaultStore, which persists.
        """
        os.environ[_env_key(name)] = value

    def delete(self, name: str) -> None:
        """Remove the env var if present (#372). Idempotent; can't fail."""
        os.environ.pop(_env_key(name), None)


class AzureKeyVaultStore:
    """Resolves secrets from Azure Key Vault via DefaultAzureCredential."""

    def __init__(self, vault_url: str) -> None:
        self._vault_url = vault_url
        self._client: SecretClient | None = None
        self._lock = threading.Lock()

    def _client_lazy(self) -> SecretClient:
        if self._client is not None:
            return self._client
        with self._lock:
            if self._client is None:
                from azure.identity import DefaultAzureCredential
                from azure.keyvault.secrets import SecretClient

                self._client = SecretClient(
                    vault_url=self._vault_url,
                    credential=DefaultAzureCredential(),
                )
            return self._client

    def get(self, name: str) -> str:
        try:
            secret = self._client_lazy().get_secret(name)
        except Exception as exc:
            raise SecretNotFoundError(
                f"Key Vault secret {name!r} at {self._vault_url}: {exc}"
            ) from exc
        value = secret.value
        if value is None:
            raise SecretNotFoundError(f"Key Vault secret {name!r} has no value")
        return str(value)

    def set(self, name: str, value: str) -> None:
        try:
            self._client_lazy().set_secret(name, value)
        except Exception as exc:
            raise SecretWriteError(
                f"Key Vault secret {name!r} at {self._vault_url}: {exc}"
            ) from exc

    def delete(self, name: str) -> None:
        """Best-effort soft-delete (#372). A missing secret is a clean no-op; any
        other failure is logged, never raised (orphan cleanup must not 500 the
        entity delete). Fires the delete; doesn't block on the soft-delete poller."""
        from azure.core.exceptions import ResourceNotFoundError

        try:
            self._client_lazy().begin_delete_secret(name)
        except ResourceNotFoundError:
            # Already absent (or soft-deleted) — deletion is idempotent, nothing to do.
            return
        except Exception as exc:
            log.warning("secret_delete_failed", name=name, error=str(exc))


class RedisSecretStore:
    """Resolves secrets from Redis — dev/test only, plaintext, shared across processes.

    The point (vs `EnvSecretStore`): Redis is shared, so a secret `set` by the API
    process is visible to the Celery worker, which is what connection-driven worker
    runs need (#86). Values are stored in **plaintext** — never use in production;
    production uses `AzureKeyVaultStore`. The redis client is lazy-built.
    """

    def __init__(self, redis_url: str, *, key_prefix: str = _REDIS_KEY_PREFIX) -> None:
        self._url = redis_url
        self._key_prefix = key_prefix
        self._client: redis.Redis[str] | None = None
        self._lock = threading.Lock()

    def _key(self, name: str) -> str:
        return f"{self._key_prefix}{name}"

    def _client_lazy(self) -> redis.Redis[str]:
        if self._client is not None:
            return self._client
        with self._lock:
            if self._client is None:
                self._client = redis.Redis.from_url(self._url, decode_responses=True)
            return self._client

    def get(self, name: str) -> str:
        try:
            value = self._client_lazy().get(self._key(name))
        except Exception as exc:
            raise SecretNotFoundError(f"Redis secret {name!r}: {exc}") from exc
        if value is None:
            raise SecretNotFoundError(f"Redis secret {name!r} not set")
        return str(value)

    def set(self, name: str, value: str) -> None:
        try:
            self._client_lazy().set(self._key(name), value)
        except Exception as exc:
            raise SecretWriteError(f"Redis secret {name!r}: {exc}") from exc

    def delete(self, name: str) -> None:
        """Best-effort delete (#372); a missing key is a no-op, failures are logged."""
        try:
            self._client_lazy().delete(self._key(name))
        except Exception as exc:
            log.warning("secret_delete_failed", name=name, error=str(exc))


_store_singleton: SecretStore | None = None
_store_lock = threading.Lock()


def _build_store(settings: Settings) -> SecretStore:
    if settings.secret_store == _AKV_MODE:
        if not settings.azure_key_vault_url:
            raise RuntimeError(f"secret_store={_AKV_MODE!r} requires AZURE_KEY_VAULT_URL")
        return AzureKeyVaultStore(settings.azure_key_vault_url)
    if settings.secret_store == _REDIS_MODE:
        return RedisSecretStore(settings.redis_url)
    return EnvSecretStore()


def get_secret_store() -> SecretStore:
    """Return the configured store (cached after first call)."""
    global _store_singleton
    if _store_singleton is not None:
        return _store_singleton
    with _store_lock:
        if _store_singleton is None:
            settings = get_settings()
            _store_singleton = _build_store(settings)
            log.info("secret_store_initialized", backend=settings.secret_store)
        return _store_singleton


def reset_secret_store_cache() -> None:
    """Test-only: clear the cached store so the next call rebuilds it."""
    global _store_singleton
    with _store_lock:
        _store_singleton = None
