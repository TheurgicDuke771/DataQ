"""The audit hash-chain's external anchor seam — ADR 0041 §9 / #1460."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import ValidationError
from structlog.testing import capture_logs

from backend.app.core.config import Settings, get_settings
from backend.app.core.tamper_anchor import (
    NoopTamperAnchor,
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
