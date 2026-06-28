"""Tests for the PII redactor (post-2026-05-28 security audit additions)."""

import logging as std_logging
from collections.abc import Iterator

import pytest

from backend.app.core.config import get_settings
from backend.app.core.logging import _redact_pii, configure_logging


def _redact(payload: dict[str, object]) -> dict[str, object]:
    """Apply the redactor as a free function (mirrors structlog processor invocation)."""
    return dict(_redact_pii(None, "", dict(payload)))


def test_redacts_credentials_and_personal_contact() -> None:
    out = _redact(
        {
            "event": "auth_attempt",
            "password": "hunter2",
            "token": "abc.def",
            "api_key": "sk-1234",
            "authorization": "Bearer xyz",
            "email": "user@example.com",
            "phone": "+15551234567",
        }
    )
    assert out["password"] == "<redacted>"
    assert out["token"] == "<redacted>"
    assert out["api_key"] == "<redacted>"
    assert out["authorization"] == "<redacted>"
    assert out["email"] == "<redacted>"
    assert out["phone"] == "<redacted>"
    assert out["event"] == "auth_attempt"  # safe key


def test_redacts_azure_ad_claim_fields() -> None:
    """Per security audit 2026-05-28: AAD identifiers are GDPR personal data."""
    out = _redact(
        {
            "event": "auth_user_resolved",
            "oid": "00000000-0000-0000-0000-000000000001",
            "aad_oid": "00000000-0000-0000-0000-000000000001",
            "aad_object_id": "00000000-0000-0000-0000-000000000001",
            "upn": "user@tenant.onmicrosoft.com",
            "preferred_username": "user@example.com",
            "user_id": "u-12345",
            "name": "Jane Doe",
            "display_name": "Jane Doe",
        }
    )
    assert out["oid"] == "<redacted>"
    assert out["aad_oid"] == "<redacted>"
    assert out["aad_object_id"] == "<redacted>"
    assert out["upn"] == "<redacted>"
    assert out["preferred_username"] == "<redacted>"
    assert out["user_id"] == "<redacted>"
    assert out["name"] == "<redacted>"
    assert out["display_name"] == "<redacted>"


def test_redacts_nested_pii_keys() -> None:
    out = _redact(
        {
            "event": "request",
            "headers": {
                "authorization": "Bearer xyz",
                "x-request-id": "safe-value",
            },
        }
    )
    assert out["headers"] == {  # type: ignore[comparison-overlap]
        "authorization": "<redacted>",
        "x-request-id": "safe-value",
    }


def test_redacts_lists_of_dicts() -> None:
    out = _redact(
        {
            "event": "failure_sample",
            "rows": [
                {"email": "a@b.com", "order_id": "ORD-1"},
                {"email": "c@d.com", "order_id": "ORD-2"},
            ],
        }
    )
    assert out["rows"] == [
        {"email": "<redacted>", "order_id": "ORD-1"},
        {"email": "<redacted>", "order_id": "ORD-2"},
    ]


def test_safe_keys_pass_through() -> None:
    """Status, level, duration etc. must not be touched."""
    out = _redact(
        {
            "event": "request",
            "method": "GET",
            "path": "/healthz",
            "status": 200,
            "duration_ms": 12.34,
            "level": "info",
        }
    )
    assert out["method"] == "GET"
    assert out["path"] == "/healthz"
    assert out["status"] == 200
    assert out["duration_ms"] == 12.34
    assert out["level"] == "info"


@pytest.fixture
def _restore_root_logging() -> Iterator[None]:
    """Snapshot/restore the root logger so the App Insights integration test below
    doesn't leak its handler into the rest of the suite."""
    root = std_logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    yield
    for h in root.handlers:
        if h not in saved_handlers:
            h.close()
    root.handlers, root.level = saved_handlers, saved_level


class _FakeAzureLogHandler(std_logging.Handler):
    """Stand-in for opencensus's AzureLogHandler that reproduces ONLY the bug-
    relevant behaviour — `createLock()` nulls `self.lock` — with no network,
    statsbeat thread, or App Insights export. Lets the test drive the real
    `configure_logging()` code path (so it catches a revert of the fix) without
    constructing the real, network-touching handler."""

    def __init__(self, connection_string: str) -> None:
        super().__init__()  # stdlib __init__ calls self.createLock() -> nulls it

    def createLock(self) -> None:
        self.lock = None  # type: ignore[assignment]  # mirrors opencensus's override


def test_azure_handler_lock_survives_createlock_recall(
    monkeypatch: pytest.MonkeyPatch, _restore_root_logging: None
) -> None:
    """#405 (a #393 recurrence): Celery's embedded beat (`worker -B`) re-initialises
    logging in its forked process and calls the AzureLogHandler's createLock() again.
    opencensus's createLock sets `self.lock = None`; on Py3.13 that makes
    logging.Handler.handle()'s `with self.lock` crash on the first record — killing
    beat (and every periodic task) on its 'beat: Starting...' line. The handler must
    keep a real lock no matter how often createLock() is called.

    Uses a fake handler (above) so configure_logging()'s override is exercised
    without real opencensus network/statsbeat behaviour."""
    import opencensus.ext.azure.log_exporter as az_log_exporter

    monkeypatch.setattr(az_log_exporter, "AzureLogHandler", _FakeAzureLogHandler)
    monkeypatch.setattr(
        get_settings(),
        "applicationinsights_connection_string",
        "InstrumentationKey=00000000-0000-0000-0000-000000000000",
    )
    configure_logging()
    ai = [h for h in std_logging.getLogger().handlers if isinstance(h, _FakeAzureLogHandler)]
    assert ai, "App Insights handler was not attached when a connection string is set"
    handler = ai[0]

    handler.createLock()  # simulate the beat fork re-initialising logging
    assert handler.lock is not None, "createLock() re-nulled the lock — beat would crash"

    # The exact prod crash path: handle() acquires `with self.lock`. Don't export.
    monkeypatch.setattr(handler, "emit", lambda record: None)
    record = std_logging.LogRecord(
        "celery.beat", std_logging.INFO, __file__, 1, "beat: Starting...", None, None
    )
    handler.handle(record)  # must not raise
