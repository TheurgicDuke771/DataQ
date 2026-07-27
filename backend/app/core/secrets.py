"""Secret resolution abstraction.

Three backends are supported, picked from `settings.secret_store`:

- **EnvSecretStore** — reads secrets from env vars prefixed `KV_SECRET_`.
  Host-only dev — convenient when running without a vault or an Azure tenant.
  Name normalisation: `snowflake-uat-finance` → env var
  `KV_SECRET_SNOWFLAKE_UAT_FINANCE`. **Per-process**: a secret written via
  `set` is only visible to the writing process (#86), so it cannot serve a
  compose stack where the API and the Celery worker are separate containers.

- **OpenBaoSecretStore** — reads/writes secrets over the **KV v2 HTTP API**
  (ADR 0039). The default for both compose stacks: it is shared across
  processes (the API writes a credential, the worker reads it — #86) and,
  unlike the plaintext Redis store it replaced, it encrypts at rest and puts
  an auth boundary and an audit log in front of the values.

  The contract is the *API*, not a vendor: the same mode works against
  OpenBao (what we ship — MPL-2.0), Vault Community/Enterprise, or HCP Vault.
  DataQ never distributes a BUSL-licensed server (CONTRIBUTING rule 40).

- **AzureKeyVaultStore** — reads from Azure Key Vault via
  `azure-identity` (DefaultAzureCredential) + `azure-keyvault-secrets`.
  The production default for DataQ's own Azure deployment (ADR 0024), where
  managed identity means there is no bootstrap credential to hold at all.

The Azure SDK is **lazy-imported** so deployments that don't use it don't pay
the import cost.
"""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable
from urllib.parse import quote

import httpx

from backend.app.core.config import Settings, get_settings
from backend.app.core.logging import get_logger

if TYPE_CHECKING:
    from azure.keyvault.secrets import SecretClient

log = get_logger(__name__)

ENV_PREFIX: Final = "KV_SECRET_"
_AKV_MODE: Final = "azure_key_vault"
_OPENBAO_MODE: Final = "openbao"
_REDIS_MODE: Final = "redis"  # removed — ADR 0039; retained only for the shim below

# KV v2 stores a MAP per path; DataQ's model is one opaque string per name, so every
# value lives under one fixed field. Changing this orphans every existing secret.
_KV_FIELD: Final = "value"
# Bounded timeout (seconds) — a degraded vault must not stall a request thread or a
# Celery task. Mirrors marquez.py's `_TIMEOUT_SECONDS` rationale.
_HTTP_TIMEOUT_SECONDS: Final = 5.0


def _explain_status(status: int) -> str:
    """Turn a KV v2 status into a cause an operator can act on.

    The point is that these are NOT "secret missing" — see `OpenBaoSecretStore`.
    """
    if status == 403:
        return "permission denied — token invalid, expired, or lacking a policy for this path"
    if status == 503:
        return "vault sealed or standby — unseal it, or point OPENBAO_ADDR at the active node"
    return "unexpected vault response"


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


class OpenBaoSecretStore:
    """Resolves secrets over the KV v2 HTTP API — OpenBao, Vault, or HCP (ADR 0039).

    Deliberately speaks the **API and not a vendor SDK**: the three endpoints DataQ
    needs are small and stable, and binding to the wire contract is what lets one
    mode serve OpenBao (what we ship), Vault Community/Enterprise, and HCP Vault
    without DataQ taking a position on the operator's server. Trading Azure lock-in
    for HashiCorp lock-in would have missed the point of ADR 0010.

        GET    /v1/{mount}/data/{name}      → value at .data.data.{_KV_FIELD}
        POST   /v1/{mount}/data/{name}      → body {"data": {_KV_FIELD: value}}
        DELETE /v1/{mount}/metadata/{name}  → purges every version

    **Failure modes stay distinguishable** (ADR 0039 decision 6). A missing secret
    is 404; a dead/expired token is 403; a sealed or unreachable vault is 503 or a
    transport error. The Protocol only lets us raise `SecretNotFoundError` here, so
    the last two additionally emit a WARNING and name the cause in the message —
    reporting "credential missing" for what is really "cannot reach the vault" is
    precisely how #954's two dead Snowflake PATs stayed invisible until someone
    read worker logs.
    """

    def __init__(
        self,
        addr: str,
        token: str,
        *,
        mount: str = "secret",
        timeout: float = _HTTP_TIMEOUT_SECONDS,
        client: httpx.Client | None = None,
    ) -> None:
        # Trailing slash stripped so the path join is unambiguous (mirrors marquez.py).
        self._addr = addr.rstrip("/")
        self._token = token
        self._mount = mount.strip("/")
        self._timeout = timeout
        # Injectable so tests can drive the real httpx request/response stack through
        # a MockTransport — the URL building, header encoding, status handling and
        # JSON decoding below are then genuinely exercised, not stubbed past.
        self._client = client
        self._lock = threading.Lock()

    def _client_lazy(self) -> httpx.Client:
        """One pooled client, built on first use (house pattern — see the AKV store).

        A connection pool matters here: every check run resolves its connection's
        credential, so a fresh TCP+TLS handshake per secret would be paid on the hot
        path. The token is a client-level header — it travels in `X-Vault-Token`,
        never the URL, since a query-string credential lands in access logs (the
        ADR 0006 `?token=` shape the logging redactor exists to cover).
        """
        if self._client is not None:
            return self._client
        with self._lock:
            if self._client is None:
                self._client = httpx.Client(base_url=self._addr, timeout=self._timeout)
            return self._client

    def _headers(self) -> dict[str, str]:
        """Auth travels **per request**, not baked into the client.

        Deliberate: an injected client (tests, or any future caller supplying its own
        pool) would otherwise silently carry no credential and every call would 403.
        Binding auth to the request rather than the transport makes that impossible.
        OpenBao keeps Vault's `X-Vault-Token` header name for API compatibility.
        """
        return {"X-Vault-Token": self._token}

    def _path(self, kind: str, name: str) -> str:
        """KV v2 splits the value plane (`data`) from the version plane (`metadata`).

        `name` is path-quoted with no safe characters: a secret name is caller data
        (`Connection.secret_ref`), and an unescaped `/` would silently retarget the
        read/write at a different KV path.
        """
        return f"/v1/{self._mount}/{kind}/{quote(name, safe='')}"

    def _log_transport_failure(self, name: str, status: int | None, error: str) -> None:
        """Emit an operator-visible signal for the not-a-missing-secret failures."""
        if status == 403:
            log.warning("openbao_permission_denied", name=name, status=status)
        elif status is None or status >= 500:
            log.warning("openbao_unreachable", name=name, status=status, error=error)

    def get(self, name: str) -> str:
        try:
            response = self._client_lazy().get(self._path("data", name), headers=self._headers())
        except httpx.HTTPError as exc:
            self._log_transport_failure(name, None, str(exc))
            raise SecretNotFoundError(
                f"OpenBao secret {name!r} at {self._addr}: vault unreachable ({exc})"
            ) from exc
        if response.status_code == 404:
            # Genuinely absent, or soft-deleted (KV v2 returns 404 for both, so no body
            # inspection is needed to tell them apart). This is the ONLY status that
            # means "no such secret".
            raise SecretNotFoundError(f"OpenBao secret {name!r} not set")
        if response.status_code != 200:
            self._log_transport_failure(name, response.status_code, response.text)
            raise SecretNotFoundError(
                f"OpenBao secret {name!r} at {self._addr}: unreadable "
                f"(HTTP {response.status_code} — {_explain_status(response.status_code)})"
            )
        try:
            value = response.json()["data"]["data"][_KV_FIELD]
        except (ValueError, KeyError, TypeError) as exc:
            raise SecretNotFoundError(
                f"OpenBao secret {name!r} has no {_KV_FIELD!r} field "
                f"(not written by DataQ?): {exc}"
            ) from exc
        if value is None:
            raise SecretNotFoundError(f"OpenBao secret {name!r} has no value")
        return str(value)

    def set(self, name: str, value: str) -> None:
        try:
            response = self._client_lazy().post(
                self._path("data", name),
                headers=self._headers(),
                json={"data": {_KV_FIELD: value}},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            self._log_transport_failure(name, status, exc.response.text)
            raise SecretWriteError(
                f"OpenBao secret {name!r} at {self._addr}: unwritable "
                f"(HTTP {status} — {_explain_status(status)})"
            ) from exc
        except httpx.HTTPError as exc:
            self._log_transport_failure(name, None, str(exc))
            raise SecretWriteError(
                f"OpenBao secret {name!r} at {self._addr}: vault unreachable ({exc})"
            ) from exc

    def delete(self, name: str) -> None:
        """Best-effort **purge** of every version (#372); missing is a no-op, fail-soft.

        Targets `metadata/` rather than `data/` deliberately: `data/` soft-deletes only
        the latest version and leaves the value recoverable, and the caller here is
        orphan cleanup after the owning connection was deleted — leaving a recoverable
        warehouse credential behind a deleted entity is the wrong default. (Key Vault
        soft-deletes because purge protection is a vault-level policy there, not ours.)
        """
        try:
            response = self._client_lazy().delete(
                self._path("metadata", name), headers=self._headers()
            )
        except httpx.HTTPError as exc:
            log.warning("secret_delete_failed", name=name, error=str(exc))
            return
        # KV v2 answers 204 even for a name that never existed — deletion is already
        # idempotent, so only a real error is worth a line.
        if response.status_code not in (200, 204, 404):
            log.warning(
                "secret_delete_failed",
                name=name,
                status=response.status_code,
                error=_explain_status(response.status_code),
            )


_store_singleton: SecretStore | None = None
_store_lock = threading.Lock()


def _build_store(settings: Settings) -> SecretStore:
    if settings.secret_store == _AKV_MODE:
        if not settings.azure_key_vault_url:
            raise RuntimeError(f"secret_store={_AKV_MODE!r} requires AZURE_KEY_VAULT_URL")
        return AzureKeyVaultStore(settings.azure_key_vault_url)
    if settings.secret_store == _OPENBAO_MODE:
        if not settings.openbao_addr:
            raise RuntimeError(f"secret_store={_OPENBAO_MODE!r} requires OPENBAO_ADDR")
        if not settings.openbao_token:
            raise RuntimeError(f"secret_store={_OPENBAO_MODE!r} requires OPENBAO_TOKEN")
        return OpenBaoSecretStore(
            settings.openbao_addr,
            settings.openbao_token,
            mount=settings.openbao_mount,
        )
    if settings.secret_store == _REDIS_MODE:
        # ADR 0039 removed the plaintext Redis store. Fail LOUDLY with the migration
        # path rather than letting pydantic emit a bare "Input should be 'env',
        # 'openbao' or 'azure_key_vault'" that names no cause — the mode is still in
        # the Literal for one cycle purely so this message is the one operators see.
        raise RuntimeError(
            "secret_store='redis' was removed in ADR 0039 — the store kept credentials "
            "in plaintext. Use SECRET_STORE=openbao (set OPENBAO_ADDR + OPENBAO_TOKEN; "
            "`docker compose up` starts the vault) and re-enter connection credentials. "
            "Then PURGE the old plaintext values, which outlive the switch: "
            "redis-cli --scan --pattern 'dataq:secret:*' | xargs -r redis-cli del. See "
            "docs/adr/0039-openbao-self-hosted-secret-backend.md"
        )
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
