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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final, Protocol, runtime_checkable
from urllib.parse import quote

import httpx

from backend.app.core.config import _REDIS_STORE_REMOVED, Settings, get_settings
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
    if status == 404:
        # On write/delete a 404 cannot mean "absent" — it means the ROUTE is absent.
        return "no such KV mount — check OPENBAO_MOUNT"
    return "unexpected vault response"


def _is_missing_mount(response: httpx.Response) -> bool:
    """Distinguish the two things KV v2 answers 404 to.

    An absent (or soft-deleted) secret under a live mount returns an EMPTY error
    list; a mount that does not exist returns a populated one::

        secret absent   → {"errors": []}
        mount not found → {"errors": ["no handler for route \\"typo/data/x\\". …"]}

    Both shapes captured from openbao/openbao v2.6.1. The distinction matters
    because `OPENBAO_MOUNT` is operator-settable and production vaults commonly
    mount per-team paths: one typo would otherwise report EVERY credential in the
    workspace as "not set" — the exact masquerade this class exists to prevent
    (#954). A body we cannot parse is treated as the benign case, since only the
    populated-errors shape is positive evidence of a bad route.
    """
    try:
        errors = response.json().get("errors")
    except (ValueError, AttributeError):
        return False
    return bool(errors)


class SecretNotFoundError(Exception):
    """Raised when the secret genuinely is not there — and ONLY then.

    Callers legitimately treat this as a *state* ("no webhook configured", "this
    connection has no extra credential") and degrade gracefully. That is only safe
    while the store never raises it for anything else — see
    `SecretStoreUnavailableError`.
    """


class SecretStoreUnavailableError(Exception):
    """Raised when the store could not answer — deliberately NOT a subclass.

    The distinction that matters is at the **type**, not in the message. Every
    caller branches on the exception class and none reads the text
    (`admin_service._safe_secret`, `notification_service._resolve_webhook_url`,
    `connection_service._extra_secrets`, `alerting/email.py`), so a store that
    folds "the vault is sealed" into `SecretNotFoundError` makes an admin page
    render "not set", makes alert delivery skip silently, and makes a connection
    run with a credential quietly omitted — during an outage, across every
    connection at once.

    ADR 0039 §6 originally claimed the Protocol forced a single exception type.
    It does not: the Protocol below declares no exceptions at all. That premise
    was wrong, and this class is the correction — it is what actually makes the
    ADR's promise true, rather than true-in-the-log-message-only.
    """


class SecretWriteError(Exception):
    """Raised when a secret cannot be written to the backing store."""


@dataclass(frozen=True)
class SecretInfo:
    """One entry from a store listing: the name, and when the store first saw it.

    `created_at` is what makes an orphan sweep safe (#1059) — it is the only signal
    that separates "abandoned months ago" from "being written right now by a
    connection-create that has not committed yet". A store that cannot supply it
    reports ``None``, and the sweep must then refuse to purge rather than guess.
    """

    name: str
    created_at: datetime | None


def _parse_vault_timestamp(raw: object) -> datetime | None:
    """Parse a KV v2 `created_time` into an aware UTC datetime, or None.

    OpenBao/Vault emit RFC 3339 with **nanosecond** precision
    (``2026-07-27T04:53:23.123456789Z``). Verified against the pinned interpreter
    rather than assumed: Python 3.13's `fromisoformat` accepts both the `Z` suffix
    and >6 fractional digits, truncating to microseconds — so no pre-processing is
    needed, and an earlier hand-rolled truncation here was dead code justified by a
    false premise. If the Python pin ever moves backwards, this is the seam to fix.

    What IS load-bearing is returning None rather than raising: this value crosses a
    **driver boundary**, and the caller reads an unknown age as "too young to touch".
    Any surprise from the server therefore fails towards *not deleting* a credential.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


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
        # Retained solely so `close()` can release it. The credential owns its OWN
        # transport session per chained credential, separate from the SecretClient's
        # pipeline — closing only the client would free half the sockets and leave
        # the token-acquisition sessions open, i.e. the same leak class this fixes.
        self._credential: Any = None
        self._lock = threading.Lock()

    def _client_lazy(self) -> SecretClient:
        if self._client is not None:
            return self._client
        with self._lock:
            if self._client is None:
                from azure.identity import DefaultAzureCredential
                from azure.keyvault.secrets import SecretClient

                self._credential = DefaultAzureCredential()
                self._client = SecretClient(
                    vault_url=self._vault_url,
                    credential=self._credential,
                )
            return self._client

    def get(self, name: str) -> str:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            secret = self._client_lazy().get_secret(name)
        except ResourceNotFoundError as exc:
            raise SecretNotFoundError(
                f"Key Vault secret {name!r} at {self._vault_url} not set"
            ) from exc
        except Exception as exc:
            # Throttling, an expired managed identity, a network fault, a vault
            # firewall rule — none of these mean the secret is absent. This store
            # wrapped them ALL in `SecretNotFoundError` before ADR 0039, so callers
            # that degrade on "not found" have been silently mistaking a Key Vault
            # outage for "nothing configured" in production. Same bug the OpenBao
            # store would have shipped; fixed for both here.
            log.warning("keyvault_unavailable", name=name, error=str(exc))
            raise SecretStoreUnavailableError(
                f"Key Vault at {self._vault_url} could not serve {name!r}: {exc}"
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

    def list_secrets(self) -> list[SecretInfo]:
        """Enumerate the vault's secrets for the orphan sweep (#1059).

        Uses `list_properties_of_secrets`, which returns names and `created_on`
        WITHOUT the values — deliberately: a sweep has no business reading
        credentials, and fetching them would put every secret in the workspace
        through this process's memory once a day for no reason.

        Raises `SecretStoreUnavailableError` on failure rather than returning a
        partial list. A truncated listing is indistinguishable from "these secrets
        no longer exist", which in a sweep means "purge them" — so a half-answer
        here is far more dangerous than no answer.
        """
        try:
            return [
                SecretInfo(name=prop.name, created_at=prop.created_on)
                for prop in self._client_lazy().list_properties_of_secrets()
                if prop.name
            ]
        except Exception as exc:
            raise SecretStoreUnavailableError(
                f"Key Vault at {self._vault_url} could not be listed ({exc})"
            ) from exc

    def close(self) -> None:
        """Release the pooled client AND the credential (#1058). Idempotent.

        Both, because they hold **separate** transport sessions: `SecretClient.close()`
        closes only its own pipeline, while `DefaultAzureCredential` opens a session
        per credential in its chain. Closing just the client would free half the
        sockets — the same leak this method exists to fix.

        Client first, then the credential it authenticates with, so nothing is asked
        to use an already-closed credential on the way down.

        Note the cost of the rebuild this permits: the next use re-runs the whole
        `DefaultAzureCredential` discovery/token chain. Fine for a test reset; worth
        weighing if the config hot-reload caller the reset docstring anticipates
        ever lands. Same shape and same reasoning as `OpenBaoSecretStore.close` —
        see there for why this is not on the Protocol.
        """
        with self._lock:
            client, self._client = self._client, None
            credential, self._credential = self._credential, None
        if client is not None:
            client.close()
        if credential is not None:
            credential.close()


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
        # Ownership, tracked at construction: `close()` may only close a pool this
        # store built. An injected client belongs to its caller, and closing it would
        # break a caller that reuses it (or a test that asserts on it afterwards).
        self._owns_client = client is None
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

    def list_secrets(self) -> list[SecretInfo]:
        """Enumerate the mount's secrets for the orphan sweep (#1059).

        Two calls per secret, unavoidably: KV v2's LIST returns names only, so the
        creation time needs a metadata GET each. That is N+1, and acceptable only
        because this runs from a daily janitor over a workspace-sized set — never on
        a request path. The values are never fetched (`metadata`, not `data`), so a
        sweep cannot leak a credential into this process.

        Raises `SecretStoreUnavailableError` on any failure of the LIST rather than
        returning a partial list: to a sweep, "absent from the listing" means
        "purge", so a truncated answer is worse than none. A per-name metadata
        failure degrades to `created_at=None` instead, which the sweep reads as
        "too young to touch" — the same safe direction.
        """
        list_path = f"/v1/{self._mount}/metadata"
        try:
            response = self._client_lazy().get(
                list_path, headers=self._headers(), params={"list": "true"}
            )
        except httpx.HTTPError as exc:
            self._log_transport_failure("<list>", None, str(exc))
            raise SecretStoreUnavailableError(
                f"OpenBao at {self._addr} unreachable while listing ({exc})"
            ) from exc
        if response.status_code == 404 and not _is_missing_mount(response):
            # An empty KV mount answers 404 with no errors — genuinely zero secrets,
            # not a fault. Distinct from a missing mount, which falls through below.
            return []
        if response.status_code != 200:
            self._log_transport_failure("<list>", response.status_code, response.text)
            raise SecretStoreUnavailableError(
                f"OpenBao at {self._addr} could not be listed: "
                f"{response.status_code} — {_explain_status(response.status_code)}"
            )
        try:
            keys = response.json()["data"]["keys"]
        except (ValueError, KeyError, TypeError) as exc:
            raise SecretStoreUnavailableError(
                f"OpenBao at {self._addr} returned an unreadable listing ({exc})"
            ) from exc
        return [
            SecretInfo(name=key, created_at=self._created_at(key))
            for key in keys
            # KV v2 lists nested paths as a trailing-slash "directory" entry. DataQ
            # writes flat names, so those are somebody else's secrets — skip rather
            # than report them as orphan candidates.
            if isinstance(key, str) and not key.endswith("/")
        ]

    def _created_at(self, name: str) -> datetime | None:
        """`created_time` from a secret's metadata, or None if it can't be read.

        Fail-soft on purpose — see `list_secrets`. None means the sweep treats the
        secret as too young to purge, so a metadata hiccup can never cause a delete.
        """
        try:
            response = self._client_lazy().get(
                self._path("metadata", name), headers=self._headers()
            )
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        try:
            return _parse_vault_timestamp(response.json()["data"]["created_time"])
        except (ValueError, KeyError, TypeError):
            return None

    def close(self) -> None:
        """Release the pooled client, if this store built it (#1058).

        Deliberately NOT on the `SecretStore` Protocol. 32 test doubles implement
        that Protocol structurally and the `backend/tests` mypy gate (#418) checks
        them, so adding a method there would force 32 no-op `close()` bodies to buy
        one real one — churn out of proportion to the fix. `reset_secret_store_cache`
        therefore duck-types it, which is also what lets any future store opt in
        without touching the seam.

        Idempotent, and safe to call on a store that is used again afterwards: an
        owned handle is cleared, so `_client_lazy` rebuilds a fresh pool on next use.

        A NOT-owned client is left entirely alone — not closed *and not detached*.
        Detaching looks harmless but is worse than closing: the next call would
        quietly build a real `httpx.Client(base_url=self._addr)` and open live
        sockets to the vault, so a store injected with a `MockTransport` would
        start doing real network I/O after any reset instead of failing loudly.
        `reset_secret_store_cache` runs from an autouse fixture, so that would have
        been a whole-suite hazard.
        """
        with self._lock:
            if not self._owns_client:
                return
            client, self._client = self._client, None
        if client is not None:
            client.close()

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
        """Emit an operator-visible signal for every not-a-missing-secret failure.

        Reached with a 404 only when the MOUNT is missing — `get` returns before this
        for an absent secret, and on write/delete a 404 can only ever be the route.

        The final `else` is load-bearing: an earlier version logged only 403 and 5xx,
        which left the whole 400-499 band silent — and that band is where the likely
        misconfigurations live. **400** is what a KV **v1** mount answers ("Invalid
        path for a versioned K/V secrets engine"), **401** is what a gateway in front
        of the vault returns when the vault's own 403 never reaches us, and **429** is
        HCP rate-limiting under check-run load, where every connection resolves a
        credential on the hot path. Silent is the one thing none of them may be.
        """
        if status == 403:
            log.warning("openbao_permission_denied", name=name, status=status)
        elif status == 404:
            log.warning("openbao_mount_missing", name=name, mount=self._mount, status=status)
        elif status is None:
            log.warning("openbao_unreachable", name=name, status=status, error=error)
        elif status >= 500:
            # The vault ANSWERED — a different investigation from a refused
            # connection, so it does not share the `unreachable` event name.
            log.warning("openbao_server_error", name=name, status=status, error=error)
        else:
            log.warning("openbao_unexpected_status", name=name, status=status, error=error)

    def get(self, name: str) -> str:
        try:
            response = self._client_lazy().get(self._path("data", name), headers=self._headers())
        except httpx.HTTPError as exc:
            self._log_transport_failure(name, None, str(exc))
            raise SecretStoreUnavailableError(
                f"OpenBao at {self._addr} unreachable while reading {name!r} ({exc})"
            ) from exc
        if response.status_code == 404 and not _is_missing_mount(response):
            # Genuinely absent, or soft-deleted — KV v2 returns 404 for both, so no
            # further body inspection is needed to tell THOSE apart. This is the ONLY
            # path in this method that may raise `SecretNotFoundError`; a 404 from a
            # missing mount falls through, because reporting a typo'd OPENBAO_MOUNT as
            # "credential missing" is the failure this class exists to prevent.
            raise SecretNotFoundError(f"OpenBao secret {name!r} not set")
        if response.status_code != 200:
            self._log_transport_failure(name, response.status_code, response.text)
            raise SecretStoreUnavailableError(
                f"OpenBao at {self._addr} could not serve {name!r} "
                f"(HTTP {response.status_code} — {_explain_status(response.status_code)})"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            # A 200 whose body is not JSON means we are not talking to the vault at
            # all — a proxy error page, a captive portal, something else on :8200.
            # That is an availability fault, not a malformed secret.
            self._log_transport_failure(name, response.status_code, "non-JSON body")
            raise SecretStoreUnavailableError(
                f"OpenBao at {self._addr} returned a non-JSON 200 for {name!r} "
                f"— is OPENBAO_ADDR pointing at the vault? ({exc})"
            ) from exc
        try:
            data = payload["data"]["data"]
        except (KeyError, TypeError) as exc:
            # The KV **v1** envelope is `{"data": {...}}` with no inner "data", so this
            # is the shape a v1 mount produces — a configuration fault, not a foreign
            # secret, and it must not be reported as one.
            self._log_transport_failure(name, response.status_code, "unexpected envelope")
            raise SecretStoreUnavailableError(
                f"OpenBao at {self._addr} returned an unexpected envelope for {name!r} "
                f"— is {self._mount!r} a KV v2 mount? ({exc})"
            ) from exc
        if not isinstance(data, dict) or _KV_FIELD not in data:
            raise SecretNotFoundError(
                f"OpenBao secret {name!r} has no {_KV_FIELD!r} field (not written by DataQ?)"
            )
        value = data[_KV_FIELD]
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
            # Route through the shared handler as well as the delete-specific line: a
            # connection delete is often the FIRST vault call after a token expires,
            # which makes it the cheapest early warning available — and an operator
            # alerting on `openbao_permission_denied` would never see it if this path
            # only ever emitted `secret_delete_failed`.
            self._log_transport_failure(name, None, str(exc))
            log.warning("secret_delete_failed", name=name, error=str(exc))
            return
        # KV v2 answers 204 even for a name that never existed — deletion is already
        # idempotent, so only a real error is worth a line.
        if response.status_code not in (200, 204, 404):
            self._log_transport_failure(name, response.status_code, response.text)
            log.warning(
                "secret_delete_failed",
                name=name,
                status=response.status_code,
                error=_explain_status(response.status_code),
                # The purge did NOT happen, so the credential is still live in the
                # vault behind a now-deleted entity — the exact state ADR 0039 §7
                # chose the metadata delete to avoid. Fail-soft keeps the entity
                # delete working; this flag is what makes the leftover findable.
                credential_still_present=True,
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
        # ADR 0039 removed the plaintext Redis store. `Settings` already rejects this
        # at startup with the same message; this is the backstop for a caller that
        # hand-builds a settings object (tests, scripts) and so never ran that
        # validator. The mode stays in the Literal for one cycle purely so operators
        # get THIS message instead of a bare "Input should be 'env', 'openbao' or
        # 'azure_key_vault'" that names no cause.
        raise RuntimeError(_REDIS_STORE_REMOVED)
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
    """Test-only: clear the cached store so the next call rebuilds it.

    Closes the outgoing store's connection pool first (#1058). Test-only today, so
    each leaked pool was one idle connection — but the leak is in the *reset*, not
    in the tests, so it would become real the moment this is reused for config
    hot-reload, which is exactly the plausible next caller.

    `close()` is duck-typed rather than declared on the Protocol (see
    `OpenBaoSecretStore.close`), and is called OUTSIDE the lock: it does socket I/O,
    and the singleton is already detached, so *rebuilding* is independent of how
    long the close takes.

    That safety is about rebuilds, not about requests already in flight. Callers
    hold a local reference to the store (`worker/tasks.py` resolves one per task),
    and `_client_lazy` reads the handle outside the lock, so a concurrent `close()`
    can still shut a pool between that read and the request. Harmless while this is
    test-only and single-threaded; it is a genuine constraint for the config
    hot-reload caller named above, which would be the multithreaded case.

    **Fail-soft**, mirroring `SecretStore.delete`: this runs from an autouse test
    fixture on every setup and teardown, so letting a store's `close()` raise would
    turn one transport hiccup into a suite-wide cascade of errors. Releasing a pool
    is cleanup; cleanup must not be the thing that fails.
    """
    global _store_singleton
    with _store_lock:
        store, _store_singleton = _store_singleton, None
    close = getattr(store, "close", None)
    if callable(close):
        try:
            close()
        except Exception as exc:
            log.warning("secret_store_close_failed", error=str(exc))
