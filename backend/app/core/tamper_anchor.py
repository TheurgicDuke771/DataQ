"""The external anchor for audit-chain tamper-evidence — ADR 0041 §9 / #1460.

Same shape as `SecretStore`/the lineage provider seam: a `Protocol`, a dark-by-
default `NoopTamperAnchor`, and concrete BYOL-safe implementations. Publishing
here is the load-bearing half of tamper-evidence — the hash chain alone only
proves nothing was rewritten to someone who can already read the whole table;
an actor who can also write it can recompute the chain forward from wherever
they edited. Anchoring the head somewhere outside the database is what makes
that recomputation detectable.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

import httpx

from backend.app.core.config import Settings, get_settings
from backend.app.core.logging import get_logger
from backend.app.core.s3_endpoint import addressing_config_kwargs, normalize_endpoint_url
from backend.app.core.secrets import get_secret_store

log = get_logger(__name__)

_WEBHOOK_MODE = "webhook"
_OBJECT_LOCK_MODE = "object_lock"

#: A publish must not hang the beat task indefinitely.
_PUBLISH_TIMEOUT_SECONDS = 10.0

#: `read_anchors` reads one object per anchor; bound the admin verify call.
DEFAULT_READ_LIMIT = 200


def anchor_payload(
    *, label: str, head_hash: str, event_count: int, as_of: datetime
) -> dict[str, Any]:
    """The one record every anchor class publishes — the webhook signs it, the
    object-lock anchor stores it, verification reads it back.
    """
    return {
        "label": label,
        "head_hash": head_hash,
        "event_count": event_count,
        "as_of": as_of.isoformat(),
    }


class TamperAnchorUnavailableError(Exception):
    """The anchor store could not be read. Distinct from "no anchors found" on
    purpose: an outage must never be reportable as a state (the ADR 0039 lesson).
    """


@runtime_checkable
class TamperAnchor(Protocol):
    def publish(self, *, label: str, head_hash: str, event_count: int, as_of: datetime) -> bool:
        """Publish the current chain head. Returns whether it was actually sent
        somewhere outside the database — never raises; a failed publish is a
        logged, non-fatal event (see callers: neither the retention purge nor
        the daily anchor task may block on this).
        """
        ...


class NoopTamperAnchor:
    """The default. Says so out loud, once per process, rather than silently
    doing nothing — the same "dark by default" honesty as `LINEAGE_PROVIDER`
    unset.
    """

    def __init__(self) -> None:
        self._warned = False
        self._lock = threading.Lock()

    def publish(self, *, label: str, head_hash: str, event_count: int, as_of: datetime) -> bool:
        with self._lock:
            if not self._warned:
                log.warning(
                    "tamper_anchor_unconfigured",
                    detail=(
                        "TAMPER_ANCHOR is unset — the audit hash chain is computed "
                        "and verifiable for accidental corruption, but an actor with "
                        "database write access could recompute it forward from any "
                        "point they edit. Set TAMPER_ANCHOR=webhook or object_lock to "
                        "anchor the head outside the database."
                    ),
                )
                self._warned = True
        return False


class WebhookTamperAnchor:
    """POSTs the head hash, HMAC-SHA256-signed, to an operator-controlled URL —
    a separate log sink, an append-only object store's ingest endpoint, whatever
    the operator's regime requires. DataQ never reads it back; the anchor's
    entire value is that DataQ does NOT control it.
    """

    def __init__(self, url: str, secret: str, *, client: httpx.Client | None = None) -> None:
        self._url = url
        self._secret = secret.encode("utf-8")
        self._client = client or httpx.Client(timeout=_PUBLISH_TIMEOUT_SECONDS)

    def _sign(self, body: bytes) -> str:
        return hmac.new(self._secret, body, hashlib.sha256).hexdigest()

    def publish(self, *, label: str, head_hash: str, event_count: int, as_of: datetime) -> bool:
        payload = anchor_payload(
            label=label, head_hash=head_hash, event_count=event_count, as_of=as_of
        )
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        try:
            response = self._client.post(
                self._url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-DataQ-Signature": f"sha256={self._sign(body)}",
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.error("tamper_anchor_publish_failed", url=self._url, error=str(exc))
            return False
        return True


# ───────────────────── Object-lock anchor (#2310) ─────────────────────


@dataclass(frozen=True)
class StoredObject:
    key: str
    #: S3 object version — a delete marker hides the current version from a plain
    #: listing; the locked version underneath it is what verification reads.
    version_id: str | None = None


class AnchorObjectStore(Protocol):
    """A WORM object store: write-once with retention, list, read."""

    def put(self, key: str, body: bytes, *, retain_until: datetime) -> None: ...

    def list_objects(self, prefix: str) -> list[StoredObject]: ...

    def fetch(self, objects: list[StoredObject]) -> list[bytes]:
        """Read each object's body, in order, over one client."""
        ...


@dataclass(frozen=True)
class AnchorRecord:
    key: str
    label: str | None
    head_hash: str | None
    event_count: int | None
    as_of: datetime | None
    retain_until: datetime | None
    #: The object under the anchor prefix is not an anchor DataQ wrote.
    malformed: bool = False


@dataclass(frozen=True)
class AnchorReadResult:
    records: list[AnchorRecord]
    #: More anchors exist than were read — the newest `limit` were.
    truncated: bool


def _parse_timestamp(value: Any) -> datetime | None:
    """An aware datetime, or None — a naive one cannot be ordered against the chain."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _parse_record(key: str, body: bytes) -> AnchorRecord:
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    head = data.get("head_hash") if isinstance(data, dict) else None
    as_of = _parse_timestamp(data.get("as_of")) if isinstance(data, dict) else None
    if not isinstance(data, dict) or not isinstance(head, str) or not head or as_of is None:
        return AnchorRecord(key, None, None, None, None, None, malformed=True)
    count = data.get("event_count")
    label = data.get("label")
    return AnchorRecord(
        key=key,
        label=label if isinstance(label, str) else None,
        head_hash=head,
        event_count=count if isinstance(count, int) else None,
        as_of=as_of,
        retain_until=_parse_timestamp(data.get("retain_until")),
    )


class ObjectLockAnchor:
    """Writes each chain head as its own immutable object — S3 Object Lock (AWS or any
    S3-compatible store that implements it, e.g. MinIO) or an Azure immutable blob.
    Unlike the webhook, DataQ CAN read this anchor back, which is what lets
    `verify_anchors` detect a database restored to before a published head.
    """

    def __init__(
        self,
        store: AnchorObjectStore,
        *,
        prefix: str,
        retention_days: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._prefix = prefix.strip("/")
        self._retention = timedelta(days=retention_days)
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def prefix(self) -> str:
        return self._prefix

    def object_key(self, published_at: datetime, head_hash: str) -> str:
        return f"{self._prefix}/{published_at:%Y%m%dT%H%M%S%f}Z-{head_hash}.json"

    def publish(self, *, label: str, head_hash: str, event_count: int, as_of: datetime) -> bool:
        now = self._clock()
        retain_until = now + self._retention
        payload = anchor_payload(
            label=label, head_hash=head_hash, event_count=event_count, as_of=as_of
        )
        payload["published_at"] = now.isoformat()
        payload["retain_until"] = retain_until.isoformat()
        key = self.object_key(now, head_hash)
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        try:
            self._store.put(key, body, retain_until=retain_until)
        except Exception as exc:
            log.error(
                "tamper_anchor_publish_failed",
                mode=_OBJECT_LOCK_MODE,
                key=key,
                error=f"{type(exc).__name__}: {exc}",
            )
            return False
        return True

    def read_anchors(self, *, limit: int = DEFAULT_READ_LIMIT) -> AnchorReadResult:
        """The newest `limit` anchors. Raises `TamperAnchorUnavailableError` when the
        store cannot be listed or read.
        """
        try:
            objects = self._store.list_objects(self._prefix + "/")
            # Keys start with the publish timestamp, so a key sort is a time sort.
            objects.sort(key=lambda o: o.key, reverse=True)
            truncated = len(objects) > limit
            page = objects[:limit]
            records = [
                _parse_record(obj.key, body)
                for obj, body in zip(page, self._store.fetch(page), strict=True)
            ]
        except Exception as exc:
            log.error(
                "tamper_anchor_read_failed",
                mode=_OBJECT_LOCK_MODE,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise TamperAnchorUnavailableError(f"{type(exc).__name__}: {exc}") from exc
        return AnchorReadResult(records=records, truncated=truncated)


class S3ObjectLockStore:
    """`put_object` with Object Lock retention. The secret access key comes from the
    SecretStore on every call (rotation is picked up without a restart) and is
    handed to boto3 as a connect argument, never placed in a URL.
    """

    def __init__(
        self, settings: Settings, *, client_factory: Callable[..., Any] | None = None
    ) -> None:
        self._bucket = settings.tamper_anchor_object_lock_bucket
        self._mode = settings.tamper_anchor_object_lock_mode
        self._region = settings.tamper_anchor_object_lock_region.strip() or None
        self._access_key_id = settings.tamper_anchor_object_lock_access_key_id
        self._secret_name = settings.tamper_anchor_object_lock_secret_name
        self._endpoint_url = normalize_endpoint_url(settings.tamper_anchor_object_lock_endpoint_url)
        self._addressing_style = settings.tamper_anchor_object_lock_addressing_style
        self._client_factory = client_factory

    def _client(self) -> Any:
        import boto3
        from botocore.config import Config

        factory = self._client_factory or boto3.client
        return factory(
            "s3",
            region_name=self._region,
            aws_access_key_id=self._access_key_id,
            aws_secret_access_key=get_secret_store().get(self._secret_name),
            endpoint_url=self._endpoint_url,
            config=Config(
                connect_timeout=_PUBLISH_TIMEOUT_SECONDS,
                read_timeout=_PUBLISH_TIMEOUT_SECONDS,
                retries={"max_attempts": 2},
                **addressing_config_kwargs(self._endpoint_url, self._addressing_style),
            ),
        )

    def put(self, key: str, body: bytes, *, retain_until: datetime) -> None:
        self._client().put_object(
            Bucket=self._bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
            ObjectLockMode=self._mode,
            ObjectLockRetainUntilDate=retain_until,
        )

    def list_objects(self, prefix: str) -> list[StoredObject]:
        client = self._client()
        found: list[StoredObject] = []
        for page in client.get_paginator("list_object_versions").paginate(
            Bucket=self._bucket, Prefix=prefix
        ):
            found.extend(
                StoredObject(v["Key"], v.get("VersionId")) for v in page.get("Versions", [])
            )
        return found

    def fetch(self, objects: list[StoredObject]) -> list[bytes]:
        client = self._client()
        bodies: list[bytes] = []
        for obj in objects:
            kwargs: dict[str, Any] = {"Bucket": self._bucket, "Key": obj.key}
            if obj.version_id and obj.version_id != "null":
                kwargs["VersionId"] = obj.version_id
            body = client.get_object(**kwargs)["Body"]
            try:
                bodies.append(bytes(body.read()))
            finally:
                body.close()
        return bodies


_AZURE_POLICY_MODE = {"COMPLIANCE": "Locked", "GOVERNANCE": "Unlocked"}


class AzureImmutableBlobStore:
    """`upload_blob` with a version-level immutability policy (the container must have
    version-level immutability enabled). The SAS token or service-principal secret
    comes from the SecretStore; the client is built by the ADLS datasource's one
    credential-to-client seam, so an auth mode is added there, not here.
    """

    def __init__(
        self, settings: Settings, *, client_factory: Callable[[], Any] | None = None
    ) -> None:
        self._container = settings.tamper_anchor_object_lock_bucket
        self._policy_mode = _AZURE_POLICY_MODE[settings.tamper_anchor_object_lock_mode]
        self._legal_hold = settings.tamper_anchor_object_lock_legal_hold
        self._secret_name = settings.tamper_anchor_object_lock_secret_name
        self._config = {
            "account_url": settings.tamper_anchor_object_lock_account_url,
            "container": settings.tamper_anchor_object_lock_bucket,
            "auth_type": settings.tamper_anchor_object_lock_auth_type,
            "tenant_id": settings.tamper_anchor_object_lock_tenant_id or None,
            "client_id": settings.tamper_anchor_object_lock_client_id or None,
        }
        self._client_factory = client_factory

    def _service_client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        from backend.app.datasources.adls import AdlsConfig, blob_service_client

        return blob_service_client(
            AdlsConfig.model_validate(self._config),
            get_secret_store().get(self._secret_name),
            connection_timeout=_PUBLISH_TIMEOUT_SECONDS,
            read_timeout=_PUBLISH_TIMEOUT_SECONDS,
        )

    def put(self, key: str, body: bytes, *, retain_until: datetime) -> None:
        from azure.storage.blob import ContentSettings, ImmutabilityPolicy

        service = self._service_client()
        try:
            service.get_container_client(self._container).upload_blob(
                name=key,
                data=body,
                overwrite=False,
                content_settings=ContentSettings(content_type="application/json"),
                immutability_policy=ImmutabilityPolicy(
                    expiry_time=retain_until, policy_mode=self._policy_mode
                ),
                legal_hold=True if self._legal_hold else None,
            )
        finally:
            service.close()

    def list_objects(self, prefix: str) -> list[StoredObject]:
        service = self._service_client()
        try:
            container = service.get_container_client(self._container)
            # Every version, like the S3 store: an overwrite or a soft delete leaves the
            # immutable content in a prior version a current-only listing never returns.
            return [
                StoredObject(b.name, getattr(b, "version_id", None))
                for b in container.list_blobs(name_starts_with=prefix, include=["versions"])
            ]
        finally:
            service.close()

    def fetch(self, objects: list[StoredObject]) -> list[bytes]:
        service = self._service_client()
        try:
            container = service.get_container_client(self._container)
            return [
                bytes(
                    container.download_blob(obj.key, version_id=obj.version_id).readall()
                    if obj.version_id
                    else container.download_blob(obj.key).readall()
                )
                for obj in objects
            ]
        finally:
            service.close()


def build_object_lock_anchor(settings: Settings) -> ObjectLockAnchor:
    store: AnchorObjectStore
    if settings.tamper_anchor_object_lock_provider == "azure":
        store = AzureImmutableBlobStore(settings)
    else:
        store = S3ObjectLockStore(settings)
    return ObjectLockAnchor(
        store,
        prefix=settings.tamper_anchor_object_lock_prefix,
        retention_days=settings.tamper_anchor_object_lock_retention_days,
    )


_anchor_singleton: TamperAnchor | None = None
_anchor_lock = threading.Lock()


def _build_anchor() -> TamperAnchor:
    settings = get_settings()
    if settings.tamper_anchor == _WEBHOOK_MODE:
        return WebhookTamperAnchor(
            settings.tamper_anchor_webhook_url, settings.tamper_anchor_webhook_secret
        )
    if settings.tamper_anchor == _OBJECT_LOCK_MODE:
        return build_object_lock_anchor(settings)
    return NoopTamperAnchor()


def get_tamper_anchor() -> TamperAnchor:
    """Return the configured anchor (cached after first call)."""
    global _anchor_singleton
    if _anchor_singleton is not None:
        return _anchor_singleton
    with _anchor_lock:
        if _anchor_singleton is None:
            _anchor_singleton = _build_anchor()
        return _anchor_singleton


def reset_tamper_anchor_cache() -> None:
    """Test-only: clear the cached anchor so the next call rebuilds it."""
    global _anchor_singleton
    with _anchor_lock:
        _anchor_singleton = None
