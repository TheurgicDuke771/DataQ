"""The audit hash-chain's external anchor seam — ADR 0041 §9 / #1460."""

from __future__ import annotations

import hashlib
import hmac
import io
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import ValidationError
from structlog.testing import capture_logs

from backend.app.core.config import Settings, get_settings
from backend.app.core.tamper_anchor import (
    AzureImmutableBlobStore,
    NoopTamperAnchor,
    ObjectLockAnchor,
    S3ObjectLockStore,
    StoredObject,
    TamperAnchorUnavailableError,
    WebhookTamperAnchor,
    get_tamper_anchor,
    reset_tamper_anchor_cache,
)

_AS_OF = datetime(2026, 1, 1, tzinfo=UTC)


# ───────────────────────── NoopTamperAnchor ─────────────────────────


def test_noop_publish_returns_false() -> None:
    """`False` means "not actually anchored anywhere outside the database" —
    callers (the purge checkpoint, the daily beat task) must be able to tell
    dark-by-default apart from a successful publish.
    """
    anchor = NoopTamperAnchor()
    assert anchor.publish(label="x", head_hash="abc", event_count=1, as_of=_AS_OF) is False


def test_noop_warns_exactly_once_across_multiple_publishes() -> None:
    """The warning must be loud enough to notice but not so loud it drowns the
    log on every single audit write.
    """
    anchor = NoopTamperAnchor()
    with capture_logs() as logs:
        anchor.publish(label="x", head_hash="a", event_count=1, as_of=_AS_OF)
        anchor.publish(label="x", head_hash="b", event_count=2, as_of=_AS_OF)
        anchor.publish(label="x", head_hash="c", event_count=3, as_of=_AS_OF)
    warnings = [entry for entry in logs if entry.get("event") == "tamper_anchor_unconfigured"]
    assert len(warnings) == 1


# ───────────────────────── WebhookTamperAnchor ─────────────────────────


def test_webhook_publish_signs_the_body_and_posts_it() -> None:
    captured_headers: dict[str, str] = {}
    captured_body: bytes = b""

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_body
        captured_headers.update(request.headers)
        captured_body = request.content
        return httpx.Response(200)

    anchor = WebhookTamperAnchor(
        "http://sink.example/ingest",
        "sekrit",
        client=httpx.Client(transport=httpx.MockTransport(_handler)),
    )

    published = anchor.publish(
        label="audit_chain_daily", head_hash="deadbeef", event_count=42, as_of=_AS_OF
    )

    assert published is True
    signature = captured_headers["x-dataq-signature"]
    assert signature.startswith("sha256=")
    # Re-derive it independently rather than importing `_sign` — proves the SIGNATURE
    # matches the BODY actually sent, not just that some header was set.
    expected = hmac.new(b"sekrit", captured_body, hashlib.sha256).hexdigest()
    assert signature == f"sha256={expected}"


def test_webhook_publish_returns_false_and_logs_on_http_error() -> None:
    anchor = WebhookTamperAnchor(
        "http://sink.example/ingest",
        "sekrit",
        client=httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(500))),
    )

    with capture_logs() as logs:
        published = anchor.publish(
            label="audit_chain_daily", head_hash="deadbeef", event_count=1, as_of=_AS_OF
        )

    assert published is False
    assert any(entry.get("event") == "tamper_anchor_publish_failed" for entry in logs)


def test_webhook_publish_survives_a_connection_error() -> None:
    def _handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    anchor = WebhookTamperAnchor(
        "http://sink.example/ingest",
        "sekrit",
        client=httpx.Client(transport=httpx.MockTransport(_handler)),
    )

    published = anchor.publish(
        label="audit_chain_daily", head_hash="deadbeef", event_count=1, as_of=_AS_OF
    )

    assert published is False


# ───────────────────────── Factory ─────────────────────────


@pytest.fixture(autouse=True)
def _reset_caches() -> Iterator[None]:
    get_settings.cache_clear()
    reset_tamper_anchor_cache()
    yield
    get_settings.cache_clear()
    reset_tamper_anchor_cache()


def test_get_tamper_anchor_defaults_to_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAMPER_ANCHOR", raising=False)
    assert isinstance(get_tamper_anchor(), NoopTamperAnchor)


def test_get_tamper_anchor_webhook_mode_builds_a_webhook_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAMPER_ANCHOR", "webhook")
    monkeypatch.setenv("TAMPER_ANCHOR_WEBHOOK_URL", "http://sink.example/ingest")
    monkeypatch.setenv("TAMPER_ANCHOR_WEBHOOK_SECRET", "sekrit")
    assert isinstance(get_tamper_anchor(), WebhookTamperAnchor)


def test_get_tamper_anchor_caches_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAMPER_ANCHOR", raising=False)
    assert get_tamper_anchor() is get_tamper_anchor()


# ───────────────────────── Settings validation ─────────────────────────


def test_webhook_mode_requires_url_and_secret() -> None:
    with pytest.raises(ValidationError, match="TAMPER_ANCHOR_WEBHOOK_URL"):
        Settings(_env_file=None, tamper_anchor="webhook")


def test_webhook_mode_requires_url_and_secret_together() -> None:
    with pytest.raises(ValidationError, match="TAMPER_ANCHOR_WEBHOOK_SECRET"):
        Settings(_env_file=None, tamper_anchor="webhook", tamper_anchor_webhook_url="http://x")


def test_webhook_url_must_have_a_scheme() -> None:
    with pytest.raises(ValidationError, match="http:// or https://"):
        Settings(
            _env_file=None,
            tamper_anchor="webhook",
            tamper_anchor_webhook_url="sink.example/ingest",
            tamper_anchor_webhook_secret="sekrit",
        )


def test_none_mode_needs_nothing() -> None:
    Settings(_env_file=None, tamper_anchor="none")  # must not raise


# ───────────────────── ObjectLockAnchor (#2310) ─────────────────────


class _FakeStore:
    """A WORM store in a dict: records every put's retention, lists, reads."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.retain_until: dict[str, datetime] = {}
        self.fail_put: Exception | None = None
        self.fail_list: Exception | None = None

    def put(self, key: str, body: bytes, *, retain_until: datetime) -> None:
        if self.fail_put is not None:
            raise self.fail_put
        assert key not in self.objects, "write-once"
        self.objects[key] = body
        self.retain_until[key] = retain_until

    def list_objects(self, prefix: str) -> list[StoredObject]:
        if self.fail_list is not None:
            raise self.fail_list
        return [StoredObject(k) for k in self.objects if k.startswith(prefix)]

    def fetch(self, objects: list[StoredObject]) -> list[bytes]:
        return [self.objects[o.key] for o in objects]


_NOW = datetime(2026, 10, 20, 12, 30, 45, 123456, tzinfo=UTC)


def _object_lock_anchor(store: _FakeStore, **kwargs: Any) -> ObjectLockAnchor:
    kwargs.setdefault("prefix", "audit-anchors")
    kwargs.setdefault("retention_days", 400)
    kwargs.setdefault("clock", lambda: _NOW)
    return ObjectLockAnchor(store, **kwargs)


def test_object_lock_publish_writes_one_immutable_object_per_head() -> None:
    store = _FakeStore()
    anchor = _object_lock_anchor(store)

    assert anchor.publish(
        label="audit_chain_daily", head_hash="ab" * 32, event_count=9, as_of=_AS_OF
    )

    [key] = store.objects
    assert key == f"audit-anchors/20261020T123045123456Z-{'ab' * 32}.json"
    assert store.retain_until[key] == _NOW + timedelta(days=400)
    payload = json.loads(store.objects[key])
    # The webhook's exact payload, plus when it was written and how long it is held.
    assert payload == {
        "label": "audit_chain_daily",
        "head_hash": "ab" * 32,
        "event_count": 9,
        "as_of": _AS_OF.isoformat(),
        "published_at": _NOW.isoformat(),
        "retain_until": (_NOW + timedelta(days=400)).isoformat(),
    }


def test_object_lock_prefix_is_normalised_so_keys_never_start_with_a_slash() -> None:
    store = _FakeStore()
    _object_lock_anchor(store, prefix="/anchors/dataq/").publish(
        label="x", head_hash="h", event_count=1, as_of=_AS_OF
    )
    [key] = store.objects
    assert key.startswith("anchors/dataq/2026")


def test_object_lock_publish_failure_is_logged_loudly_and_returns_false() -> None:
    store = _FakeStore()
    store.fail_put = RuntimeError("AccessDenied")
    anchor = _object_lock_anchor(store)

    with capture_logs() as logs:
        published = anchor.publish(label="x", head_hash="h", event_count=1, as_of=_AS_OF)

    assert published is False
    [entry] = [e for e in logs if e.get("event") == "tamper_anchor_publish_failed"]
    assert entry["log_level"] == "error"
    assert entry["mode"] == "object_lock"
    assert "AccessDenied" in entry["error"]


def test_object_lock_read_anchors_returns_newest_first_and_flags_foreign_objects() -> None:
    store = _FakeStore()
    anchor = _object_lock_anchor(store)
    older = datetime(2026, 1, 1, tzinfo=UTC)
    _object_lock_anchor(store, clock=lambda: older).publish(
        label="audit_events_purge", head_hash="old", event_count=3, as_of=older
    )
    anchor.publish(label="audit_chain_daily", head_hash="new", event_count=5, as_of=_NOW)
    store.objects["audit-anchors/zzz-not-ours.json"] = b"{not json"
    store.objects["elsewhere/ignored.json"] = b"{}"

    result = anchor.read_anchors()

    assert [r.head_hash for r in result.records] == [None, "new", "old"]
    assert result.records[0].malformed is True
    assert result.records[1].label == "audit_chain_daily"
    assert result.records[1].as_of == _NOW
    assert result.records[1].retain_until == _NOW + timedelta(days=400)
    assert result.records[1].event_count == 5
    assert result.truncated is False


@pytest.mark.parametrize(
    "body",
    [
        b'{"head_hash": "h", "as_of": "2026-01-01T00:00:00"}',
        b'{"head_hash": "h", "as_of": "yesterday"}',
        b'{"head_hash": "h"}',
    ],
)
def test_an_anchor_without_an_aware_timestamp_is_malformed(body: bytes) -> None:
    """A naive `as_of` cannot be ordered against the tz-aware purge cutoff; reading
    it as valid would make verification raise instead of reporting."""
    store = _FakeStore()
    store.objects["audit-anchors/x.json"] = body

    [record] = _object_lock_anchor(store).read_anchors().records

    assert record.malformed is True


def test_object_lock_read_anchors_honours_the_limit_and_reports_truncation() -> None:
    store = _FakeStore()
    for day in range(1, 6):
        when = datetime(2026, 3, day, tzinfo=UTC)
        _object_lock_anchor(store, clock=lambda w=when: w).publish(
            label="d", head_hash=f"h{day}", event_count=day, as_of=when
        )

    result = _object_lock_anchor(store).read_anchors(limit=2)

    assert [r.head_hash for r in result.records] == ["h5", "h4"]
    assert result.truncated is True


def test_object_lock_read_failure_raises_unavailable_not_empty() -> None:
    """An outage must never read as "no anchors published" (the ADR 0039 lesson)."""
    store = _FakeStore()
    store.fail_list = ConnectionError("endpoint down")

    with capture_logs() as logs, pytest.raises(TamperAnchorUnavailableError, match="endpoint down"):
        _object_lock_anchor(store).read_anchors()

    assert any(e.get("event") == "tamper_anchor_read_failed" for e in logs)


def _object_lock_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "tamper_anchor": "object_lock",
        "tamper_anchor_object_lock_provider": "s3",
        "tamper_anchor_object_lock_bucket": "anchors",
        "tamper_anchor_object_lock_secret_name": "anchor-s3-key",
        "tamper_anchor_object_lock_access_key_id": "AKIA",
        "tamper_anchor_object_lock_region": "eu-west-1",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class _CapturingS3Client:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def put_object(self, **kwargs: Any) -> None:
        self.calls.append(("put_object", kwargs))

    def get_paginator(self, name: str) -> Any:
        assert name == "list_object_versions"
        calls = self.calls

        class _Paginator:
            def paginate(self, **kwargs: Any) -> list[dict[str, Any]]:
                calls.append(("list_object_versions", kwargs))
                return [
                    {"Versions": [{"Key": "audit-anchors/a.json", "VersionId": "v1"}]},
                    {"Versions": [{"Key": "audit-anchors/b.json", "VersionId": "null"}]},
                ]

        return _Paginator()

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get_object", kwargs))
        return {"Body": io.BytesIO(b'{"head_hash": "h"}')}


def test_s3_store_puts_with_object_lock_retention_and_reads_the_secret_from_the_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KV_SECRET_ANCHOR_S3_KEY", "the-secret-access-key")
    built: list[_CapturingS3Client] = []

    def _factory(service: str, **kwargs: Any) -> _CapturingS3Client:
        assert service == "s3"
        client = _CapturingS3Client(**kwargs)
        built.append(client)
        return client

    settings = _object_lock_settings(
        tamper_anchor_object_lock_mode="GOVERNANCE",
        tamper_anchor_object_lock_endpoint_url="http://minio.local:9000/",
    )
    store = S3ObjectLockStore(settings, client_factory=_factory)
    until = _NOW + timedelta(days=1)

    store.put("audit-anchors/k.json", b"{}", retain_until=until)

    [client] = built
    assert client.kwargs["aws_access_key_id"] == "AKIA"
    assert client.kwargs["aws_secret_access_key"] == "the-secret-access-key"
    assert client.kwargs["endpoint_url"] == "http://minio.local:9000"
    assert client.kwargs["region_name"] == "eu-west-1"
    # An S3-compatible endpoint defaults to path addressing (#1063).
    assert client.kwargs["config"].s3 == {"addressing_style": "path"}
    assert client.calls == [
        (
            "put_object",
            {
                "Bucket": "anchors",
                "Key": "audit-anchors/k.json",
                "Body": b"{}",
                "ContentType": "application/json",
                "ObjectLockMode": "GOVERNANCE",
                "ObjectLockRetainUntilDate": until,
            },
        )
    ]


def test_s3_store_lists_every_version_so_a_delete_marker_cannot_hide_an_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KV_SECRET_ANCHOR_S3_KEY", "k")
    clients: list[_CapturingS3Client] = []

    def _factory(service: str, **kwargs: Any) -> _CapturingS3Client:
        clients.append(_CapturingS3Client(**kwargs))
        return clients[-1]

    store = S3ObjectLockStore(_object_lock_settings(), client_factory=_factory)

    objects = store.list_objects("audit-anchors/")
    assert objects == [
        StoredObject("audit-anchors/a.json", "v1"),
        StoredObject("audit-anchors/b.json", "null"),
    ]
    clients.clear()
    assert store.fetch(objects) == [b'{"head_hash": "h"}', b'{"head_hash": "h"}']
    # One client (and one secret read) for the whole batch, not one per object.
    assert len(clients) == 1
    gets = [kw for c in clients for name, kw in c.calls if name == "get_object"]
    # A real version id is passed through; the unversioned "null" sentinel is not.
    assert gets == [
        {"Bucket": "anchors", "Key": "audit-anchors/a.json", "VersionId": "v1"},
        {"Bucket": "anchors", "Key": "audit-anchors/b.json"},
    ]


def test_s3_store_without_a_region_leaves_boto3_its_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KV_SECRET_ANCHOR_S3_KEY", "k")
    seen: dict[str, Any] = {}

    def _factory(service: str, **kwargs: Any) -> _CapturingS3Client:
        seen.update(kwargs)
        return _CapturingS3Client()

    settings = _object_lock_settings(
        tamper_anchor_object_lock_region="",
        tamper_anchor_object_lock_endpoint_url="https://s3.example",
    )
    S3ObjectLockStore(settings, client_factory=_factory).put("k", b"", retain_until=_NOW)
    assert seen["region_name"] is None


class _CapturingContainer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def upload_blob(self, **kwargs: Any) -> None:
        self.calls.append(("upload_blob", kwargs))

    def list_blobs(self, **kwargs: Any) -> list[Any]:
        self.calls.append(("list_blobs", kwargs))
        return [
            SimpleNamespace(name="audit-anchors/a.json", version_id="2026-10-20T00:00:00Z"),
            SimpleNamespace(name="audit-anchors/b.json", version_id=None),
        ]

    def download_blob(self, name: str, **kwargs: Any) -> Any:
        self.calls.append(("download_blob", {"name": name, **kwargs}))
        return SimpleNamespace(readall=lambda: b'{"head_hash": "h"}')


class _CapturingService:
    def __init__(self, container: _CapturingContainer) -> None:
        self.container = container
        self.closed = 0

    def get_container_client(self, name: str) -> _CapturingContainer:
        assert name == "anchors"
        return self.container

    def close(self) -> None:
        self.closed += 1


def _azure_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "tamper_anchor_object_lock_provider": "azure",
        "tamper_anchor_object_lock_account_url": "https://acct.blob.core.windows.net",
        "tamper_anchor_object_lock_access_key_id": "",
        "tamper_anchor_object_lock_region": "",
    }
    values.update(overrides)
    return _object_lock_settings(**values)


@pytest.mark.parametrize(
    ("mode", "legal_hold", "expected_policy", "expected_hold"),
    [("COMPLIANCE", False, "Locked", None), ("GOVERNANCE", True, "Unlocked", True)],
)
def test_azure_store_uploads_with_an_immutability_policy(
    mode: str, legal_hold: bool, expected_policy: str, expected_hold: bool | None
) -> None:
    """Pinned to the SDK's documented call shape — real Azure immutability is
    unverifiable without a cloud account (#2224)."""
    container = _CapturingContainer()
    service = _CapturingService(container)
    settings = _azure_settings(
        tamper_anchor_object_lock_mode=mode, tamper_anchor_object_lock_legal_hold=legal_hold
    )
    store = AzureImmutableBlobStore(settings, client_factory=lambda: service)
    until = _NOW + timedelta(days=30)

    store.put("audit-anchors/k.json", b"{}", retain_until=until)

    [(name, kwargs)] = container.calls
    assert name == "upload_blob"
    assert kwargs["name"] == "audit-anchors/k.json"
    assert kwargs["data"] == b"{}"
    assert kwargs["overwrite"] is False
    assert kwargs["content_settings"].content_type == "application/json"
    assert kwargs["immutability_policy"].expiry_time == until
    assert kwargs["immutability_policy"].policy_mode == expected_policy
    assert kwargs["legal_hold"] is expected_hold
    assert service.closed == 1


def test_azure_store_lists_and_reads_blobs_under_the_prefix() -> None:
    container = _CapturingContainer()
    service = _CapturingService(container)
    store = AzureImmutableBlobStore(_azure_settings(), client_factory=lambda: service)

    objects = store.list_objects("audit-anchors/")
    assert objects == [
        StoredObject("audit-anchors/a.json", "2026-10-20T00:00:00Z"),
        StoredObject("audit-anchors/b.json"),
    ]
    assert store.fetch(objects) == [b'{"head_hash": "h"}', b'{"head_hash": "h"}']
    # Every version, like S3: an overwrite or soft delete must not hide a locked anchor.
    assert container.calls[0] == (
        "list_blobs",
        {"name_starts_with": "audit-anchors/", "include": ["versions"]},
    )
    assert container.calls[1:] == [
        ("download_blob", {"name": "audit-anchors/a.json", "version_id": "2026-10-20T00:00:00Z"}),
        ("download_blob", {"name": "audit-anchors/b.json"}),
    ]
    assert service.closed == 2


def test_azure_store_builds_its_client_through_the_adls_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One credential-to-client door (`adls.blob_service_client`), not a second one."""
    from backend.app.datasources import adls

    monkeypatch.setenv("KV_SECRET_ANCHOR_S3_KEY", "sp-client-secret")
    seen: dict[str, Any] = {}

    def _fake(config: Any, secret: str, **kwargs: Any) -> Any:
        seen["config"] = config
        seen["secret"] = secret
        seen["kwargs"] = kwargs
        return _CapturingService(_CapturingContainer())

    monkeypatch.setattr(adls, "blob_service_client", _fake)
    settings = _azure_settings(
        tamper_anchor_object_lock_auth_type="service_principal",
        tamper_anchor_object_lock_tenant_id="contoso.onmicrosoft.com",
        tamper_anchor_object_lock_client_id="app-id",
    )

    AzureImmutableBlobStore(settings).list_objects("audit-anchors/")

    assert seen["secret"] == "sp-client-secret"
    assert seen["config"].auth_type == "service_principal"
    assert seen["config"].tenant_id == "contoso.onmicrosoft.com"
    assert seen["config"].container == "anchors"
    assert seen["kwargs"]["connection_timeout"] > 0


def test_get_tamper_anchor_object_lock_mode_builds_the_right_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAMPER_ANCHOR", "object_lock")
    monkeypatch.setenv("TAMPER_ANCHOR_OBJECT_LOCK_BUCKET", "anchors")
    monkeypatch.setenv("TAMPER_ANCHOR_OBJECT_LOCK_SECRET_NAME", "anchor-key")
    monkeypatch.setenv("TAMPER_ANCHOR_OBJECT_LOCK_ACCESS_KEY_ID", "AKIA")
    monkeypatch.setenv("TAMPER_ANCHOR_OBJECT_LOCK_REGION", "us-east-2")
    anchor = get_tamper_anchor()
    assert isinstance(anchor, ObjectLockAnchor)
    assert isinstance(anchor._store, S3ObjectLockStore)

    get_settings.cache_clear()
    reset_tamper_anchor_cache()
    monkeypatch.setenv("TAMPER_ANCHOR_OBJECT_LOCK_PROVIDER", "azure")
    monkeypatch.setenv("TAMPER_ANCHOR_OBJECT_LOCK_ACCOUNT_URL", "https://a.blob.core.windows.net")
    anchor = get_tamper_anchor()
    assert isinstance(anchor, ObjectLockAnchor)
    assert isinstance(anchor._store, AzureImmutableBlobStore)


# ───────────────────── object_lock Settings validation ─────────────────────


@pytest.mark.parametrize(
    ("overrides", "missing"),
    [
        ({"tamper_anchor_object_lock_bucket": ""}, "TAMPER_ANCHOR_OBJECT_LOCK_BUCKET"),
        ({"tamper_anchor_object_lock_secret_name": " "}, "TAMPER_ANCHOR_OBJECT_LOCK_SECRET_NAME"),
        (
            {"tamper_anchor_object_lock_access_key_id": ""},
            "TAMPER_ANCHOR_OBJECT_LOCK_ACCESS_KEY_ID",
        ),
        ({"tamper_anchor_object_lock_region": ""}, "TAMPER_ANCHOR_OBJECT_LOCK_REGION"),
    ],
)
def test_object_lock_s3_mode_fails_boot_on_an_incomplete_config(
    overrides: dict[str, Any], missing: str
) -> None:
    with pytest.raises(ValidationError, match=missing):
        _object_lock_settings(**overrides)


def test_object_lock_s3_region_is_optional_with_an_endpoint() -> None:
    _object_lock_settings(
        tamper_anchor_object_lock_region="",
        tamper_anchor_object_lock_endpoint_url="http://minio:9000",
    )


def test_object_lock_endpoint_refuses_an_embedded_credential() -> None:
    with pytest.raises(ValidationError, match=r"ENDPOINT_URL.*must not embed a credential"):
        _object_lock_settings(tamper_anchor_object_lock_endpoint_url="http://ak:sk@minio:9000")


@pytest.mark.parametrize("days", [0, -1])
def test_object_lock_retention_must_be_at_least_a_day(days: int) -> None:
    with pytest.raises(ValidationError, match="RETENTION_DAYS"):
        _object_lock_settings(tamper_anchor_object_lock_retention_days=days)


def test_object_lock_prefix_must_not_be_blank() -> None:
    with pytest.raises(ValidationError, match="PREFIX"):
        _object_lock_settings(tamper_anchor_object_lock_prefix="//")


def test_object_lock_mode_is_a_closed_vocabulary() -> None:
    with pytest.raises(ValidationError):
        _object_lock_settings(tamper_anchor_object_lock_mode="FOREVER")


@pytest.mark.parametrize(
    ("overrides", "missing"),
    [
        ({"tamper_anchor_object_lock_account_url": ""}, "TAMPER_ANCHOR_OBJECT_LOCK_ACCOUNT_URL"),
        (
            {"tamper_anchor_object_lock_auth_type": "service_principal"},
            "TAMPER_ANCHOR_OBJECT_LOCK_TENANT_ID and TAMPER_ANCHOR_OBJECT_LOCK_CLIENT_ID",
        ),
    ],
)
def test_object_lock_azure_mode_fails_boot_on_an_incomplete_config(
    overrides: dict[str, Any], missing: str
) -> None:
    with pytest.raises(ValidationError, match=missing):
        _azure_settings(**overrides)


def test_object_lock_azure_account_url_must_be_https() -> None:
    with pytest.raises(ValidationError, match="ACCOUNT_URL must start with https://"):
        _azure_settings(tamper_anchor_object_lock_account_url="http://acct.blob.core.windows.net")


def test_object_lock_azure_mode_does_not_need_the_s3_fields() -> None:
    _azure_settings()  # must not raise
