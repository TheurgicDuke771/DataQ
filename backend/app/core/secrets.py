"""Secret resolution abstraction (ADR 0039)."""

from __future__ import annotations

import math
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final, Protocol, runtime_checkable
from urllib.parse import quote

import httpx

from backend.app.core.config import _REDIS_STORE_REMOVED, Settings, get_settings
from backend.app.core.logging import get_logger
from backend.app.core.timeutil import as_utc, as_utc_or_none

if TYPE_CHECKING:
    from azure.keyvault.secrets import SecretClient

log = get_logger(__name__)

ENV_PREFIX: Final = "KV_SECRET_"
_AKV_MODE: Final = "azure_key_vault"
_ASM_MODE: Final = "aws_secrets_manager"
_OPENBAO_MODE: Final = "openbao"
_REDIS_MODE: Final = "redis"  # removed — ADR 0039; retained only for the shim below

# Every value lives under one fixed KV v2 field; changing this orphans every
# existing secret.
_KV_FIELD: Final = "value"
# Bounded so a degraded vault cannot stall a request thread or a Celery task.
_HTTP_TIMEOUT_SECONDS: Final = 5.0
# Renew an AppRole token this margin BEFORE its lease ends (#1054) — removes the
# mid-flight 403 between the staleness check and the server handling the request.
_TOKEN_RENEWAL_MARGIN_SECONDS: Final = 60
# Cap a server-reported lease before arithmetic so an absurd value cannot raise
# OverflowError out of get/set/delete (which no caller guards).
_MAX_LEASE_SECONDS: Final = 365 * 24 * 3600


def _explain_status(status: int) -> str:
    """KV v2 status → operator-actionable cause; none of these mean "secret missing"."""
    if status == 403:
        return "permission denied — token invalid, expired, or lacking a policy for this path"
    if status == 503:
        return "vault sealed or standby — unseal it, or point OPENBAO_ADDR at the active node"
    if status == 404:
        # On write/delete a 404 means the ROUTE is absent, not the secret.
        return "no such KV mount — check OPENBAO_MOUNT"
    return "unexpected vault response"


def _is_missing_mount(response: httpx.Response) -> bool:
    """Distinguish KV v2's two 404s (shapes captured from openbao v2.6.1)."""
    try:
        errors = response.json().get("errors")
    except (ValueError, AttributeError):
        return False
    return bool(errors)


class SecretNotFoundError(Exception):
    """Raised ONLY when the secret genuinely is not there — callers treat this as
    a state and degrade gracefully, so an outage must never be folded into it
    (see `SecretStoreUnavailableError`).
    """


class SecretStoreUnavailableError(Exception):
    """The store could not answer — deliberately NOT a subclass. Callers branch
    on the exception TYPE, never the message (ADR 0039 §6): folding an outage
    into `SecretNotFoundError` renders it as unconfigured state everywhere.
    """


class SecretWriteError(Exception):
    """Raised when a secret cannot be written to the backing store."""


@dataclass(frozen=True)
class SecretInfo:
    """One store-listing entry. `created_at=None` means unknown age — the orphan
    sweep (#1059) must then refuse to purge rather than guess.
    """

    name: str
    created_at: datetime | None


def _parse_vault_timestamp(raw: object) -> datetime | None:
    """Parse a KV v2 `created_time` (RFC 3339, nanosecond precision) into an
    aware UTC datetime — or None, never raising: the value crosses a driver
    boundary, and an unknown age fails towards *not deleting* a credential.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    return as_utc(parsed)


@runtime_checkable
class SecretStore(Protocol):
    def get(self, name: str) -> str: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None:
        """Best-effort removal (#372): idempotent and fail-soft — never raises
        (cleanup must not 500 the owning entity's delete); failures are logged.
        """
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
        """Write into the process env — dev only, NOT persisted across restarts."""
        os.environ[_env_key(name)] = value

    def delete(self, name: str) -> None:
        """Remove the env var if present (#372). Idempotent; can't fail."""
        os.environ.pop(_env_key(name), None)


class AzureKeyVaultStore:
    """Resolves secrets from Azure Key Vault via DefaultAzureCredential."""

    def __init__(self, vault_url: str) -> None:
        self._vault_url = vault_url
        self._client: SecretClient | None = None
        # Retained so close() can release it — the credential holds its OWN
        # transport sessions, separate from the SecretClient's pipeline.
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
            # Throttling / identity / network faults are NOT "secret absent"
            # (ADR 0039 §6).
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
        """Best-effort soft-delete (#372): missing is a clean no-op; other
        failures are logged, never raised. Doesn't block on the delete poller.
        """
        from azure.core.exceptions import ResourceNotFoundError

        try:
            self._client_lazy().begin_delete_secret(name)
        except ResourceNotFoundError:
            # Already absent — deletion is idempotent.
            return
        except Exception as exc:
            log.warning("secret_delete_failed", name=name, error=str(exc))

    def list_secrets(self) -> list[SecretInfo]:
        """Enumerate names + created_on for the orphan sweep (#1059) — never the
        values. Raises `SecretStoreUnavailableError` rather than returning a
        partial list: to the sweep, "absent from the listing" means "purge".
        """
        try:
            return [
                # `created_on` may be tz-naive — the DRIVER's choice; normalise
                # at the boundary or the sweep's aware subtraction raises (#953/#823).
                SecretInfo(name=prop.name, created_at=as_utc_or_none(prop.created_on))
                for prop in self._client_lazy().list_properties_of_secrets()
                if prop.name
            ]
        except Exception as exc:
            raise SecretStoreUnavailableError(
                f"Key Vault at {self._vault_url} could not be listed ({exc})"
            ) from exc

    def close(self) -> None:
        """Release the pooled client AND the credential (#1058); idempotent."""
        with self._lock:
            client, self._client = self._client, None
            credential, self._credential = self._credential, None
        if client is not None:
            client.close()
        if credential is not None:
            credential.close()


class AwsSecretsManagerStore:
    """Resolves secrets from AWS Secrets Manager via the task's IAM role."""

    def __init__(self, prefix: str) -> None:
        self._prefix = prefix.strip().rstrip("/")
        self._client: Any = None
        self._lock = threading.Lock()

    def _client_lazy(self) -> Any:
        if self._client is not None:
            return self._client
        with self._lock:
            if self._client is None:
                import boto3

                self._client = boto3.client("secretsmanager")
            return self._client

    def _full_name(self, name: str) -> str:
        return f"{self._prefix}/{name}"

    def get(self, name: str) -> str:
        from botocore.exceptions import ClientError

        full_name = self._full_name(name)
        try:
            response = self._client_lazy().get_secret_value(SecretId=full_name)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
                raise SecretNotFoundError(f"Secrets Manager secret {full_name!r} not set") from exc
            # Throttling / role / network faults are NOT "secret absent" (ADR 0039 §6).
            log.warning("secrets_manager_unavailable", name=full_name, error=str(exc))
            raise SecretStoreUnavailableError(
                f"Secrets Manager could not serve {full_name!r}: {exc}"
            ) from exc
        except Exception as exc:
            log.warning("secrets_manager_unavailable", name=full_name, error=str(exc))
            raise SecretStoreUnavailableError(
                f"Secrets Manager could not serve {full_name!r}: {exc}"
            ) from exc
        value = response.get("SecretString")
        if value is None:
            raise SecretNotFoundError(f"Secrets Manager secret {full_name!r} has no string value")
        return str(value)

    def set(self, name: str, value: str) -> None:
        from botocore.exceptions import ClientError

        full_name = self._full_name(name)
        # Client-construction failures (e.g. no region) must surface as
        # SecretWriteError (mapped to a 502), not bypass that mapping as a 500.
        try:
            client = self._client_lazy()
        except Exception as exc:
            raise SecretWriteError(f"Secrets Manager secret {full_name!r}: {exc}") from exc
        try:
            client.put_secret_value(SecretId=full_name, SecretString=value)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
                raise SecretWriteError(f"Secrets Manager secret {full_name!r}: {exc}") from exc
            # First write — Secrets Manager has no upsert; create, then put.
            try:
                client.create_secret(Name=full_name, SecretString=value)
            except ClientError as create_exc:
                if create_exc.response.get("Error", {}).get("Code") != "ResourceExistsException":
                    raise SecretWriteError(
                        f"Secrets Manager secret {full_name!r}: {create_exc}"
                    ) from create_exc
                # Lost the create race to a concurrent first write — the secret
                # exists now; exactly one retry of the put.
                try:
                    client.put_secret_value(SecretId=full_name, SecretString=value)
                except Exception as retry_exc:
                    raise SecretWriteError(
                        f"Secrets Manager secret {full_name!r}: {retry_exc}"
                    ) from retry_exc
            except Exception as create_exc:
                raise SecretWriteError(
                    f"Secrets Manager secret {full_name!r}: {create_exc}"
                ) from create_exc
        except Exception as exc:
            raise SecretWriteError(f"Secrets Manager secret {full_name!r}: {exc}") from exc

    def delete(self, name: str) -> None:
        """Best-effort delete (#372); missing is a no-op, fail-soft. The default
        (no ForceDelete) keeps the ~30-day recovery window — matching AKV's
        soft-delete, not OpenBao's hard purge.
        """
        from botocore.exceptions import ClientError

        full_name = self._full_name(name)
        try:
            self._client_lazy().delete_secret(SecretId=full_name)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
                return
            log.warning("secret_delete_failed", name=full_name, error=str(exc))
        except Exception as exc:
            log.warning("secret_delete_failed", name=full_name, error=str(exc))

    def list_secrets(self) -> list[SecretInfo]:
        """Enumerate this install's secrets for the orphan sweep (#1059)."""
        client = self._client_lazy()
        prefix_with_sep = f"{self._prefix}/"
        found: list[SecretInfo] = []
        next_token: str | None = None
        try:
            while True:
                kwargs: dict[str, Any] = {"Filters": [{"Key": "name", "Values": [prefix_with_sep]}]}
                if next_token:
                    kwargs["NextToken"] = next_token
                response = client.list_secrets(**kwargs)
                for entry in response.get("SecretList", []):
                    entry_name = entry.get("Name")
                    if not isinstance(entry_name, str) or not entry_name.startswith(
                        prefix_with_sep
                    ):
                        continue
                    found.append(
                        SecretInfo(
                            name=entry_name[len(prefix_with_sep) :],
                            created_at=as_utc_or_none(entry.get("CreatedDate")),
                        )
                    )
                next_token = response.get("NextToken")
                if not next_token:
                    break
        except Exception as exc:
            raise SecretStoreUnavailableError(
                f"Secrets Manager could not be listed under prefix {prefix_with_sep!r}: {exc}"
            ) from exc
        return found

    def close(self) -> None:
        """Release the pooled boto3 client (#1058); idempotent. Duck-typed (see
        `OpenBaoSecretStore.close`); guarded — older botocore builds lack close().
        """
        with self._lock:
            client, self._client = self._client, None
        if client is not None:
            close = getattr(client, "close", None)
            if callable(close):
                close()


class OpenBaoSecretStore:
    """Resolves secrets over the KV v2 HTTP API — OpenBao, Vault, or HCP (ADR 0039)."""

    def __init__(
        self,
        addr: str,
        token: str | None = None,
        *,
        role_id: str | None = None,
        secret_id: str | None = None,
        mount: str = "secret",
        timeout: float = _HTTP_TIMEOUT_SECONDS,
        client: httpx.Client | None = None,
    ) -> None:
        self._addr = addr.rstrip("/")
        # Phase-1 static token (ADR 0039 decision 4); kept for dev and
        # non-AppRole deployments.
        self._static_token = token
        # AppRole (#1054): both or neither — never a silent static-token downgrade.
        self._role_id = (role_id or "").strip() or None
        self._secret_id = (secret_id or "").strip() or None
        # Guarded by `_auth_lock`, SEPARATE from the client lock: login performs
        # a network call and must not serialise client construction.
        self._auth_token: str | None = None
        # A `time.monotonic()` DEADLINE, not wall-clock — see `_token_is_stale`.
        self._auth_expires_at: float | None = None
        self._auth_lock = threading.Lock()
        self._mount = mount.strip("/")
        self._timeout = timeout
        # Injectable so tests drive the real httpx stack through a MockTransport.
        self._client = client
        # `close()` may only close a pool this store built — an injected client
        # belongs to its caller.
        self._owns_client = client is None
        self._lock = threading.Lock()

    def _client_lazy(self) -> httpx.Client:
        """One pooled client, built on first use — credential resolution is on
        the hot path. The token travels per request in `X-Vault-Token`, never
        the URL (a query-string credential lands in access logs).
        """
        if self._client is not None:
            return self._client
        with self._lock:
            if self._client is None:
                self._client = httpx.Client(base_url=self._addr, timeout=self._timeout)
            return self._client

    def list_secrets(self) -> list[SecretInfo]:
        """Enumerate the mount's secrets for the orphan sweep (#1059)."""
        list_path = f"/v1/{self._mount}/metadata"
        try:
            response = self._send("GET", list_path, params={"list": "true"})
        except httpx.HTTPError as exc:
            self._log_transport_failure("<list>", None, str(exc))
            raise SecretStoreUnavailableError(
                f"OpenBao at {self._addr} unreachable while listing ({exc})"
            ) from exc
        if response.status_code == 404 and not _is_missing_mount(response):
            # An empty KV mount answers 404 with no errors — zero secrets, not a fault.
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
            # KV v2 lists nested paths as trailing-slash "directory" entries —
            # somebody else's secrets, not orphan candidates.
            if isinstance(key, str) and not key.endswith("/")
        ]

    def _created_at(self, name: str) -> datetime | None:
        """`created_time` from a secret's metadata, or None if unreadable —
        fail-soft, so a metadata hiccup can never cause a delete.
        """
        try:
            response = self._send("GET", self._path("metadata", name))
        except (httpx.HTTPError, SecretStoreUnavailableError):
            # Including login failure — escaping would abort the whole sweep.
            return None
        if response.status_code != 200:
            return None
        try:
            return _parse_vault_timestamp(response.json()["data"]["created_time"])
        except (ValueError, KeyError, TypeError):
            return None

    def close(self) -> None:
        """Release the pooled client, if this store built it (#1058). Idempotent."""
        with self._lock:
            if not self._owns_client:
                return
            client, self._client = self._client, None
        if client is not None:
            client.close()

    def _headers(self) -> dict[str, str]:
        """Auth travels per REQUEST, not baked into the client: an injected
        client would carry no credential, and the AppRole token changes over the
        process's life. OpenBao keeps Vault's `X-Vault-Token` header name.
        """
        token, _fresh = self._current_token()
        return {"X-Vault-Token": token}

    def _current_token(self) -> tuple[str, bool]:
        """The token to present: static, or a live AppRole token (#1054)."""
        if self._role_id is None:
            # Static-token mode; `_build_store` guarantees one of the two is set.
            return self._static_token or "", False
        with self._auth_lock:
            if self._auth_token is None or self._token_is_stale():
                self._login_locked()
                # Freshly minted: a 403 on THIS request cannot mean "expired",
                # so `_send` must not spend another login on it.
                return self._auth_token or "", True
            return self._auth_token or "", False

    def _token_is_stale(self) -> bool:
        """True when the cached token is inside the renewal margin (#1054)."""
        if self._auth_expires_at is None:
            return False
        return time.monotonic() >= self._auth_expires_at

    def _lease_seconds(self, lease: object) -> float | None:
        """Seconds until the token should be REPLACED, or None for "never expires"."""
        if isinstance(lease, bool) or lease is None:
            return None
        try:
            seconds = float(lease)  # type: ignore[arg-type]  # narrowed by the except below
        except (TypeError, ValueError):
            log.warning("openbao_lease_unreadable", lease_type=type(lease).__name__)
            return None
        # `math.isfinite` covers NaN and both infinities; CodeQL misreads the
        # hand-rolled `x != x` idiom.
        if not math.isfinite(seconds) or seconds <= 0:
            return None
        # Cap before arithmetic so a nonsense value cannot overflow the clock.
        seconds = min(seconds, _MAX_LEASE_SECONDS)
        return max(seconds - _TOKEN_RENEWAL_MARGIN_SECONDS, seconds / 2, 1.0)

    def _login_locked(self) -> None:
        """POST the AppRole login and cache the token. Caller holds `_auth_lock`."""
        try:
            response = self._client_lazy().post(
                "/v1/auth/approle/login",
                json={"role_id": self._role_id, "secret_id": self._secret_id},
            )
        except httpx.HTTPError as exc:
            raise SecretStoreUnavailableError(
                f"OpenBao at {self._addr} unreachable during AppRole login ({exc})"
            ) from exc
        if response.status_code != 200:
            # NEVER `response.text` here: a login error body can echo the
            # submitted secret_id — surface only the status + explanation.
            log.warning(
                "openbao_approle_login_failed",
                status=response.status_code,
                error=_explain_status(response.status_code),
            )
            raise SecretStoreUnavailableError(
                f"OpenBao AppRole login failed: {response.status_code} — "
                f"{_explain_status(response.status_code)}"
            )
        try:
            auth = response.json()["auth"]
            token = auth["client_token"]
        except (ValueError, KeyError, TypeError) as exc:
            raise SecretStoreUnavailableError(
                f"OpenBao AppRole login returned an unreadable body ({exc})"
            ) from exc
        lease = auth.get("lease_duration")
        # Expiry computed BEFORE caching, so an unreadable lease can't pair a
        # new token with the previous token's expiry.
        remaining = self._lease_seconds(lease)
        self._auth_expires_at = None if remaining is None else time.monotonic() + remaining
        self._auth_token = token
        # Never log the token itself.
        log.info("openbao_approle_login", lease_duration=lease, renewable=auth.get("renewable"))

    def _forget_token_if_current(self, sent: str) -> None:
        """Compare-and-swap, not an unconditional clear: the store is a
        process-wide singleton, and N threads 403ing together must not each
        discard a peer's fresh token and log in again.
        """
        with self._auth_lock:
            if self._auth_token == sent:
                self._auth_token = None
                self._auth_expires_at = None

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """One request, with a single re-login retry on 403 (#1054) — AppRole only."""
        token, fresh = self._current_token()
        client = self._client_lazy()
        response = client.request(method, path, headers={"X-Vault-Token": token}, **kwargs)
        if response.status_code != 403 or self._role_id is None:
            return response
        if fresh:
            # Minted for THIS request: the 403 is a policy gap, not expiry —
            # re-login would buy nothing. Surface it.
            return response
        log.info("openbao_token_rejected_relogin", path=path)
        self._forget_token_if_current(token)
        retry_token, _ = self._current_token()
        return client.request(method, path, headers={"X-Vault-Token": retry_token}, **kwargs)

    def _path(self, kind: str, name: str) -> str:
        """`data` is the value plane, `metadata` the version plane. `name` is
        path-quoted with no safe chars — it is caller data, and an unescaped
        `/` would silently retarget a different KV path.
        """
        return f"/v1/{self._mount}/{kind}/{quote(name, safe='')}"

    def _log_transport_failure(self, name: str, status: int | None, error: str) -> None:
        """Operator-visible signal for every not-a-missing-secret failure."""
        if status == 403:
            log.warning("openbao_permission_denied", name=name, status=status)
        elif status == 404:
            log.warning("openbao_mount_missing", name=name, mount=self._mount, status=status)
        elif status is None:
            log.warning("openbao_unreachable", name=name, status=status, error=error)
        elif status >= 500:
            # The vault ANSWERED — a different investigation from a refused connection.
            log.warning("openbao_server_error", name=name, status=status, error=error)
        else:
            log.warning("openbao_unexpected_status", name=name, status=status, error=error)

    def get(self, name: str) -> str:
        try:
            response = self._send("GET", self._path("data", name))
        except httpx.HTTPError as exc:
            self._log_transport_failure(name, None, str(exc))
            raise SecretStoreUnavailableError(
                f"OpenBao at {self._addr} unreachable while reading {name!r} ({exc})"
            ) from exc
        if response.status_code == 404 and not _is_missing_mount(response):
            # Absent or soft-deleted — the ONLY `SecretNotFoundError` path here; a missing-mount 404
            # falls through (a typo'd OPENBAO_MOUNT must not read as "credential missing").
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
            # A non-JSON 200 means we are not talking to the vault at all — an
            # availability fault, not a malformed secret.
            self._log_transport_failure(name, response.status_code, "non-JSON body")
            raise SecretStoreUnavailableError(
                f"OpenBao at {self._addr} returned a non-JSON 200 for {name!r} "
                f"— is OPENBAO_ADDR pointing at the vault? ({exc})"
            ) from exc
        try:
            data = payload["data"]["data"]
        except (KeyError, TypeError) as exc:
            # A KV **v1** mount's envelope has no inner "data" — a configuration
            # fault, not a foreign secret.
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
            response = self._send(
                "POST", self._path("data", name), json={"data": {_KV_FIELD: value}}
            )
            response.raise_for_status()
        except SecretStoreUnavailableError as exc:
            # `set` promises `SecretWriteError` (mapped to a 502); a login
            # failure of another type would bypass that mapping as a bare 500.
            raise SecretWriteError(
                f"OpenBao at {self._addr} could not be authenticated to write {name!r}"
            ) from exc
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
        """Best-effort purge of every version (#372); missing is a no-op, fail-soft."""
        try:
            response = self._send("DELETE", self._path("metadata", name))
        except SecretStoreUnavailableError as exc:
            # A login failure surfaces as this, not as httpx — it must not
            # escape delete's fail-soft contract, which callers rely on by name.
            log.warning("secret_delete_failed", name=name, error=str(exc))
            return
        except httpx.HTTPError as exc:
            # Shared handler too: a delete is often the first vault call after a
            # token expires — the cheapest early warning for permission alerting.
            self._log_transport_failure(name, None, str(exc))
            log.warning("secret_delete_failed", name=name, error=str(exc))
            return
        # KV v2 answers 204 even for a name that never existed.
        if response.status_code not in (200, 204, 404):
            self._log_transport_failure(name, response.status_code, response.text)
            log.warning(
                "secret_delete_failed",
                name=name,
                status=response.status_code,
                error=_explain_status(response.status_code),
                # The purge did NOT happen — the credential is still live behind
                # a deleted entity; this flag makes the leftover findable.
                credential_still_present=True,
            )


_store_singleton: SecretStore | None = None
_store_lock = threading.Lock()


def _build_store(settings: Settings) -> SecretStore:
    if settings.secret_store == _AKV_MODE:
        if not settings.azure_key_vault_url:
            raise RuntimeError(f"secret_store={_AKV_MODE!r} requires AZURE_KEY_VAULT_URL")
        return AzureKeyVaultStore(settings.azure_key_vault_url)
    if settings.secret_store == _ASM_MODE:
        if not settings.aws_secrets_manager_prefix.strip():
            raise RuntimeError(f"secret_store={_ASM_MODE!r} requires AWS_SECRETS_MANAGER_PREFIX")
        return AwsSecretsManagerStore(settings.aws_secrets_manager_prefix)
    if settings.secret_store == _OPENBAO_MODE:
        if not settings.openbao_addr:
            raise RuntimeError(f"secret_store={_OPENBAO_MODE!r} requires OPENBAO_ADDR")
        if not settings.openbao_token and not settings.openbao_role_id:
            raise RuntimeError(
                f"secret_store={_OPENBAO_MODE!r} requires OPENBAO_TOKEN, or "
                "OPENBAO_ROLE_ID + OPENBAO_SECRET_ID for AppRole auth"
            )
        if bool(settings.openbao_role_id) != bool(settings.openbao_secret_id):
            raise RuntimeError(
                "OPENBAO_ROLE_ID and OPENBAO_SECRET_ID must be set together (AppRole)"
            )
        return OpenBaoSecretStore(
            settings.openbao_addr,
            settings.openbao_token,
            role_id=settings.openbao_role_id,
            secret_id=settings.openbao_secret_id,
            mount=settings.openbao_mount,
        )
    if settings.secret_store == _REDIS_MODE:
        # Backstop for hand-built settings that skipped the Settings validator;
        # the mode stays in the Literal one cycle so operators see this message.
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
    """Test-only: clear the cached store so the next call rebuilds it."""
    global _store_singleton
    with _store_lock:
        store, _store_singleton = _store_singleton, None
    close = getattr(store, "close", None)
    if callable(close):
        try:
            close()
        except Exception as exc:
            log.warning("secret_store_close_failed", error=str(exc))
