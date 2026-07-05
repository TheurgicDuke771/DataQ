"""Tests for the PII redactor (post-2026-05-28 security audit additions)."""

import logging as std_logging
from collections.abc import Iterator
from typing import Any

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
            "passphrase": "kp-" + "x" * 8,  # any value under this key must redact
        }
    )
    assert out["password"] == "<redacted>"
    assert out["token"] == "<redacted>"
    assert out["api_key"] == "<redacted>"
    assert out["authorization"] == "<redacted>"
    assert out["email"] == "<redacted>"
    assert out["phone"] == "<redacted>"
    assert out["passphrase"] == "<redacted>"  # key-pair passphrase (#194)
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
    assert out["headers"] == {
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


def test_scrubs_secret_query_params_in_string_values() -> None:
    """#494: a token embedded in a message STRING (e.g. the ADF webhook URL) must be
    scrubbed — the key-based redaction only catches dict keys."""
    out = _redact(
        {"event": 'POST /api/v1/orchestration/events/adf?token=s3cr3t-VALUE.1 HTTP/1.1" 200'}
    )
    assert "s3cr3t-VALUE.1" not in str(out["event"])
    assert "token=<redacted>" in str(out["event"])


def test_scrubs_assorted_secret_params_but_keeps_safe_pairs() -> None:
    out = _redact({"event": "https://h/x?api_key=AAA&signature=BBB&page=2"})
    assert "AAA" not in str(out["event"]) and "BBB" not in str(out["event"])
    assert "api_key=<redacted>" in str(out["event"])
    assert "signature=<redacted>" in str(out["event"])
    assert "page=2" in str(out["event"])  # non-secret param untouched


def test_uvicorn_access_logger_is_silenced(
    monkeypatch: pytest.MonkeyPatch, _restore_root_logging: None
) -> None:
    """#494: uvicorn.access logs the raw query string (?token=…), so it must not
    propagate to the root handlers (stdout + App Insights). The request middleware
    provides a path-only structured access log instead."""
    access = std_logging.getLogger("uvicorn.access")
    saved_prop, saved_handlers = access.propagate, access.handlers[:]
    try:
        monkeypatch.setattr(get_settings(), "applicationinsights_connection_string", None)
        configure_logging()
        assert access.propagate is False
        assert access.handlers == []
    finally:
        access.propagate, access.handlers = saved_prop, saved_handlers


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


# ── OTel log export bridge (#524 — replaced the opencensus AzureLogHandler) ──


def _otel_bridge_handler(root: std_logging.Logger) -> std_logging.Handler | None:
    """The OTel LoggingHandler attached to `root` by configure_logging(), if any."""
    from opentelemetry.sdk._logs import LoggingHandler

    return next((h for h in root.handlers if isinstance(h, LoggingHandler)), None)


def _flush_bridge(root: std_logging.Logger) -> None:
    """Drain the bridge handler's BatchLogRecordProcessor to its exporter."""
    handler = _otel_bridge_handler(root)
    assert handler is not None, "OTel log bridge was not attached"
    handler._logger_provider.force_flush()  # type: ignore[attr-defined]


@pytest.fixture
def in_memory_log_exporter(monkeypatch: pytest.MonkeyPatch, _restore_root_logging: None) -> Any:
    """Route the OTel log bridge to an in-memory exporter (no network) and stub the
    process-global set_logger_provider so repeated configure_logging() calls across
    the suite don't warn/leak a provider."""
    import opentelemetry._logs as otel_logs_api
    from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter

    from backend.app.core import otel as otel_mod

    exporter = InMemoryLogRecordExporter()
    monkeypatch.setattr(otel_mod, "build_log_exporters", lambda settings=None: [exporter])
    monkeypatch.setattr(otel_logs_api, "set_logger_provider", lambda provider: None)
    return exporter


def test_otel_bridge_redacts_foreign_message_body(in_memory_log_exporter: Any) -> None:
    """#494 parity on OTel: a FOREIGN (non-structlog) record whose message embeds a
    secret is scrubbed in the exported BODY before it leaves the process — the
    LoggingHandler renders the body through the redacting ProcessorFormatter."""
    configure_logging()
    root = std_logging.getLogger()
    std_logging.getLogger("uvicorn.error").info(
        'GET /api/v1/orchestration/events/adf?token=SUPERSECRET-1 HTTP/1.1" 200'
    )
    _flush_bridge(root)
    bodies = " ".join(
        str(log.log_record.body) for log in in_memory_log_exporter.get_finished_logs()
    )
    assert "SUPERSECRET-1" not in bodies
    assert "token=<redacted>" in bodies


def test_otel_bridge_redacts_exported_attributes(in_memory_log_exporter: Any) -> None:
    """The OTel LoggingHandler exports every non-reserved record var as an OTel
    attribute, BYPASSING the body formatter — so a secret in a record `extra=` must
    be scrubbed on the ATTRIBUTE too. This is stricter than the old opencensus
    handler, which only exported the formatted message (#494/#536)."""
    configure_logging()
    root = std_logging.getLogger()
    std_logging.getLogger("some.lib").warning(
        "sample", extra={"password": "hunter2", "order_id": "ORD-1"}
    )
    _flush_bridge(root)
    attrs: dict[str, object] = {}
    for log in in_memory_log_exporter.get_finished_logs():
        attrs.update(dict(log.log_record.attributes or {}))
    assert attrs.get("password") == "<redacted>"
    assert attrs.get("order_id") == "ORD-1"  # non-secret extra preserved verbatim


def test_otel_bridge_handler_has_working_lock_and_handles(in_memory_log_exporter: Any) -> None:
    """#405-class fork-safety guard, ported to OTel. The opencensus crash was
    `createLock()` nulling `self.lock` → `with self.lock` raising on the first emit,
    killing beat (and every periodic task) on its 'beat: Starting...' line. The stdlib
    LoggingHandler keeps a real RLock, and the SDK's BatchLogRecordProcessor re-inits
    its export thread across fork (os.register_at_fork) — so that crash class cannot
    recur. Assert the handler carries a lock and handling a record does not raise."""
    configure_logging()
    handler = _otel_bridge_handler(std_logging.getLogger())
    assert handler is not None
    assert handler.lock is not None, "bridge handler has no lock — emit would crash"
    record = std_logging.LogRecord(
        "celery.beat", std_logging.INFO, __file__, 1, "beat: Starting...", None, None
    )
    handler.handle(record)  # must not raise


def test_no_otel_bridge_when_telemetry_off(
    monkeypatch: pytest.MonkeyPatch, _restore_root_logging: None
) -> None:
    """No exporter configured (no App Insights connection string) ⇒ no OTel bridge
    attaches; the stdout StreamHandler still carries logs."""
    monkeypatch.setattr(get_settings(), "applicationinsights_connection_string", None)
    configure_logging()
    assert _otel_bridge_handler(std_logging.getLogger()) is None


def test_scrubs_url_userinfo_credentials() -> None:
    """#536: a SQLAlchemy-style engine URL carries the credential in the URL
    USERINFO (`scheme://user:secret@host`), not a query param — the #494 regex
    missed that shape entirely."""
    out = _redact(
        {
            "event": "engine url databricks://token:dapiDEADBEEF123@dbc-x.cloud.databricks.com"
            "?http_path=/sql/1.0/warehouses/x"
        }
    )
    assert "dapiDEADBEEF123" not in str(out["event"])
    assert "databricks://token:<redacted>@dbc-x.cloud.databricks.com" in str(out["event"])
    # Non-credential URLs are untouched.
    out2 = _redact({"event": "see https://docs.example.com/path and postgres://host/db"})
    assert out2["event"] == "see https://docs.example.com/path and postgres://host/db"


def test_exception_tracebacks_drop_frame_locals_and_scrub_messages(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """#536: frame locals (which can hold anything in scope — engine URLs with
    embedded credentials, sample rows) must NOT be serialized into the log
    event, and the rendered exception strings pass the scrubber."""
    configure_logging()
    logger = std_logging.getLogger("test.locals.leak")
    try:
        engine_url = "databricks://token:dapiSHOULDNOTLEAK@host/x"  # the leaking local
        raise RuntimeError(
            f"connect failed for databricks://token:dapiALSONOT@host ({len(engine_url)})"
        )
    except RuntimeError:
        logger.exception("boom")
    line = capsys.readouterr().out
    assert "dapiSHOULDNOTLEAK" not in line  # locals not captured at all
    assert '"locals"' not in line
    assert "dapiALSONOT" not in line  # exception MESSAGE passed the scrubber
    assert "token:<redacted>@" in line
    assert "RuntimeError" in line  # the traceback itself is still there
