"""Secret resolution abstraction.

Two backends are supported, picked from `settings.secret_store`:

- **EnvSecretStore** — reads secrets from env vars prefixed `KV_SECRET_`.
  Local dev only — convenient when running against `docker-compose` without
  an Azure tenant. Name normalisation: `snowflake-uat-finance` →
  env var `KV_SECRET_SNOWFLAKE_UAT_FINANCE`.

- **AzureKeyVaultStore** — reads from Azure Key Vault via
  `azure-identity` (DefaultAzureCredential) + `azure-keyvault-secrets`.
  Production / staging. Real vault provisioning + tenant config land in
  Week 7 (deployment hardening); the code path is wired now so callers
  can take a dependency on `SecretStore` without waiting.

The Azure SDK is **lazy-imported** so EnvSecretStore-only deployments
don't pay the import cost.
"""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

from backend.app.core.config import Settings, get_settings
from backend.app.core.logging import get_logger

if TYPE_CHECKING:
    from azure.keyvault.secrets import SecretClient

log = get_logger(__name__)

ENV_PREFIX: Final = "KV_SECRET_"


class SecretNotFoundError(Exception):
    """Raised when the requested secret is missing or unreadable."""


@runtime_checkable
class SecretStore(Protocol):
    def get(self, name: str) -> str: ...


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
        if secret.value is None:
            raise SecretNotFoundError(f"Key Vault secret {name!r} has no value")
        return secret.value


_store_singleton: SecretStore | None = None
_store_lock = threading.Lock()


def _build_store(settings: Settings) -> SecretStore:
    if settings.secret_store == "azure_key_vault":
        if not settings.azure_key_vault_url:
            raise RuntimeError("secret_store='azure_key_vault' requires AZURE_KEY_VAULT_URL")
        return AzureKeyVaultStore(settings.azure_key_vault_url)
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
