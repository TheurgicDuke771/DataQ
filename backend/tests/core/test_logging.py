"""Tests for the PII redactor (post-2026-05-28 security audit additions)."""

import json
import logging as std_logging
import sys
from collections.abc import Iterator
from typing import Any

import pytest
import structlog
from opentelemetry._logs import SeverityNumber

from backend.app.alerting.base import mark_already_logged
from backend.app.core.config import get_settings
from backend.app.core.logging import (
    _downgrade_already_logged_exceptions,
    _redact_pii,
    configure_logging,
)


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


def test_redacts_the_iceberg_catalog_secret_key() -> None:
    """The Iceberg SQL/hive catalog's SECOND credential (#1181) — a distinct key
    from "secret", so it needs its own exact-match entry (the #849 lesson: don't
    rely on no call site ever logging it, redact by key regardless)."""
    out = _redact({"event": "connection_update", "catalog_secret": "hunter2-catalog"})
    assert out["catalog_secret"] == "<redacted>"


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


def test_otel_setup_failure_degrades_to_stdout_never_raises(
    monkeypatch: pytest.MonkeyPatch, _restore_root_logging: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """#628 review (HIGH): a telemetry misconfig (bad OTLP endpoint/headers, SDK
    drift) must NOT crash configure_logging() — which runs in the API lifespan and
    the celery signal handlers (the #405 blast radius). It degrades to stdout-only:
    no bridge attaches, the failure is logged, and stdout logging keeps working."""
    from backend.app.core import otel as otel_mod

    monkeypatch.setattr(otel_mod, "build_log_exporters", lambda settings=None: [object()])

    def _boom(_service_name: str) -> Any:
        raise RuntimeError("otel resource boom")

    monkeypatch.setattr(otel_mod, "build_resource", _boom)

    configure_logging()  # must not raise
    root = std_logging.getLogger()
    assert _otel_bridge_handler(root) is None  # setup failed → bridge not attached

    std_logging.getLogger("still.alive").warning("post-failure line")
    out = capsys.readouterr().out
    assert "otel_log_export_setup_failed" in out  # the degradation was recorded
    assert "post-failure line" in out  # stdout logging survives


# ── OTel raw-LogRecord downgrade for already-logged exceptions (#1261 follow-up) ──
#
# The structlog-side downgrade (`_downgrade_already_logged_exceptions`, tested
# below under "already-logged-traceback downgrade") only rewrites the
# `event_dict` — the rendered stdout JSON body. It does NOT touch the raw
# stdlib `logging.LogRecord` `log.exception()` created: `LoggingHandler.
# _translate`/`_get_attributes` (inherited by `_RedactingOTelLogHandler`,
# unmodified) read `record.levelno`/`record.exc_info` straight off that raw
# record. Verified empirically (not assumed) that these two things are true at
# once for THIS app's actual logger configuration
# (`structlog.make_filtering_bound_logger` + `structlog.stdlib.LoggerFactory`):
#
# 1. `record.levelno` is ALWAYS `ERROR` for a native `log.exception(...)` call
#    — set by which underlying stdlib method got invoked (`.error()`),
#    decided before any processor runs, and NOT reflected in that choice by
#    the processor chain's later rewrite of `event_dict["level"]`.
# 2. `record.exc_info` on that SAME raw record is ALWAYS `None` for a native
#    call, marked or not — `FilteringBoundLogger.exception()` folds
#    `exc_info=True` into the event_dict as a plain DICT KEY (not a Python
#    kwarg), and that key is consumed/dropped by the processor chain (either
#    by the downgrade processor popping it, or by the traceback renderer
#    rendering-and-popping it) before `wrap_for_formatter` ever hands
#    anything to the real stdlib `Logger.error()` call.
#
# So without `_RedactingOTelLogHandler.emit`'s own downgrade, a downgraded
# exception's OTel export keeps ERROR severity forever (reintroducing #1226's
# duplicate-alert-noise problem on the telemetry channel) — and a naive fix
# that only checks `record.exc_info` (the shape the original review
# description assumed) does NOTHING for this app's real call sites, because
# that field was never populated for them in the first place. These tests
# assert against the EXPORTED `LogRecord` (the mocked-exporter seam used by
# the rest of this OTel section), not stdout — the only place that can catch
# this class of bug (the #1282 lesson: verify against the actual artifact, not
# a proxy for it) — and the FIRST one is written to fail against a
# `record.exc_info`-only "fix", not just against no fix at all.


def test_otel_bridge_downgrades_a_marked_exception_to_warning(
    in_memory_log_exporter: Any,
) -> None:
    """The regression test for the bug this PR fixes: a marked exception logged
    via the real, native `log.exception(...)` path must export WARNING
    severity — not just render `"level": "warning"` in stdout JSON, which the
    structlog processor alone already achieved and which is exactly what let
    this bug hide behind a green test suite. Also proves the traceback text
    doesn't leak into the exported body."""
    configure_logging()
    log = structlog.get_logger("otel_downgrade_regression")
    root = std_logging.getLogger()

    exc = RuntimeError("every alert channel failed")
    mark_already_logged(exc)
    try:
        raise exc
    except RuntimeError:
        log.exception("already_reported_elsewhere")

    _flush_bridge(root)
    logs = in_memory_log_exporter.get_finished_logs()
    assert logs, "no log record reached the OTel exporter"
    record = logs[-1].log_record

    assert record.severity_number == SeverityNumber.WARN
    assert record.severity_text == "WARN"
    assert "Traceback" not in str(record.body)
    assert "every alert channel failed" not in str(record.body)


def test_otel_bridge_keeps_error_severity_for_an_unmarked_exception(
    in_memory_log_exporter: Any,
) -> None:
    """The other half of the same proof: a genuinely NEW (unmarked) exception
    logged natively must still export at full ERROR severity — the fix must
    not blind the OTel channel to real, first-reported failures — and its
    traceback must still be visible in the exported body."""
    configure_logging()
    log = structlog.get_logger("otel_downgrade_regression")
    root = std_logging.getLogger()

    try:
        raise RuntimeError("a brand new bug nobody has marked")
    except RuntimeError:
        log.exception("first_report")

    _flush_bridge(root)
    logs = in_memory_log_exporter.get_finished_logs()
    assert logs, "no log record reached the OTel exporter"
    record = logs[-1].log_record

    assert record.severity_number == SeverityNumber.ERROR
    assert record.severity_text == "ERROR"
    assert "a brand new bug nobody has marked" in str(record.body)


def test_otel_bridge_downgrades_a_marked_exception_from_a_foreign_record(
    in_memory_log_exporter: Any,
) -> None:
    """The OTHER record shape this handler must handle correctly: a bare,
    non-structlog `logging.getLogger(x).exception(...)` call bridged in via
    `foreign_pre_chain`. Unlike a native call, a foreign record's
    `record.exc_info` genuinely IS the real `(type, value, tb)` stdlib set at
    creation time (structlog never intercepted the call) — this is the shape
    the `_already_logged_exception(record.exc_info)` branch of
    `_record_marks_already_logged_exception` covers, and it's also the one
    case where the base `_get_attributes` would otherwise populate
    `exception.type`/`exception.message`/`exception.stacktrace` attributes
    directly off `record.exc_info` — so this is the path that proves those
    attributes actually get stripped, not just that severity changes."""
    configure_logging()
    root = std_logging.getLogger()
    foreign_logger = std_logging.getLogger("some.foreign.library")

    exc = RuntimeError("a foreign caller's exception, already reported")
    mark_already_logged(exc)
    try:
        raise exc
    except RuntimeError:
        foreign_logger.exception("foreign_already_reported")

    _flush_bridge(root)
    logs = in_memory_log_exporter.get_finished_logs()
    assert logs, "no log record reached the OTel exporter"
    record = logs[-1].log_record

    assert record.severity_number == SeverityNumber.WARN
    attrs = dict(record.attributes or {})
    assert "exception.type" not in attrs
    assert "exception.message" not in attrs
    assert "exception.stacktrace" not in attrs


def test_otel_bridge_keeps_error_and_attributes_for_an_unmarked_foreign_record(
    in_memory_log_exporter: Any,
) -> None:
    """The foreign-record counterpart of the native "don't blind real errors"
    proof: an unmarked foreign exception must keep ERROR severity AND its
    `exception.*` attributes (the base `LoggingHandler` behavior this handler
    must not disturb for the normal case)."""
    configure_logging()
    root = std_logging.getLogger()
    foreign_logger = std_logging.getLogger("some.other.foreign.library")

    try:
        raise RuntimeError("a brand new foreign bug nobody has marked")
    except RuntimeError:
        foreign_logger.exception("foreign_first_report")

    _flush_bridge(root)
    logs = in_memory_log_exporter.get_finished_logs()
    assert logs, "no log record reached the OTel exporter"
    record = logs[-1].log_record

    assert record.severity_number == SeverityNumber.ERROR
    attrs = dict(record.attributes or {})
    assert attrs.get("exception.type") == "RuntimeError"
    assert "a brand new foreign bug nobody has marked" in str(attrs.get("exception.message", ""))


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


# ── already-logged-traceback downgrade, centralized in the chain (#1261) ──────
#
# #1226 fixed a real bug: when every alerting channel fails, the composite
# (`CompositePublisher._fan_out_delivered_first`) logs a full traceback per
# failing channel before re-raising the last one, and the CALLER's own
# `except Exception: log.exception(...)` logged that same last-channel traceback
# a second time. The #1260 fix downgraded that with a per-caller
# `if was_already_logged(exc): log.warning(...) else: log.exception(...)` check —
# duplicated verbatim in the two callers and opt-in per caller, so a future third
# caller that forgets it silently reintroduces the bug. #1261 moves the check
# into this processor, the same shape as `_redact_pii` above: applied once, in
# the chain, to every log record — not repeated at every call site.


def test_downgrade_processor_ignores_records_with_no_exception() -> None:
    """The overwhelming majority of log calls carry no `exc_info` at all; the
    processor must be a true no-op on them, not merely non-crashing."""
    event = {"event": "suite_run_started", "suite_id": "abc"}
    out = _downgrade_already_logged_exceptions(None, "info", dict(event))
    assert out == event


def test_downgrade_processor_leaves_an_unmarked_exception_untouched() -> None:
    """An exception nobody has marked (a genuinely new bug) must keep its
    `error`/`exception` level and its `exc_info`, or the watchdog this seam
    protects goes blind to real failures."""
    caught: BaseException | None = None
    try:
        raise RuntimeError("brand new bug")
    except RuntimeError as exc:
        caught = exc
        out = _downgrade_already_logged_exceptions(
            None, "exception", {"event": "x", "exc_info": exc}
        )
    assert out["exc_info"] is caught
    assert "level" not in out


def test_downgrade_processor_downgrades_a_marked_exception_instance() -> None:
    """`exc_info` as a bare exception instance (one of the three shapes structlog
    itself supports, per `_figure_out_exc_info`) is recognized directly."""
    exc = RuntimeError("every alert channel failed")
    mark_already_logged(exc)
    out = _downgrade_already_logged_exceptions(None, "exception", {"event": "x", "exc_info": exc})
    assert out["level"] == "warning"
    assert "exc_info" not in out
    assert out["error_type"] == "RuntimeError"


def test_downgrade_processor_downgrades_a_marked_exc_info_tuple() -> None:
    """`exc_info` as a `(type, value, tb)` tuple — the shape `ProcessorFormatter`
    copies over from a foreign stdlib `LogRecord.exc_info` for a non-structlog
    logger bridged through `foreign_pre_chain` — is recognized too."""
    exc = RuntimeError("every alert channel failed")
    mark_already_logged(exc)
    try:
        raise exc
    except RuntimeError:
        exc_info_tuple = sys.exc_info()
    out = _downgrade_already_logged_exceptions(
        None, "error", {"event": "x", "exc_info": exc_info_tuple}
    )
    assert out["level"] == "warning"
    assert "exc_info" not in out


def test_downgrade_processor_downgrades_a_marked_exc_info_true() -> None:
    """`exc_info=True` — what `BoundLogger.exception()` actually sets — means
    "look up the exception currently being handled" (`sys.exc_info()`); this is
    the shape every real `log.exception(...)` call produces."""
    exc = RuntimeError("every alert channel failed")
    mark_already_logged(exc)
    try:
        raise exc
    except RuntimeError:
        out = _downgrade_already_logged_exceptions(
            None, "exception", {"event": "x", "exc_info": True}
        )
    assert out["level"] == "warning"
    assert "exc_info" not in out


def test_downgrade_processor_never_clobbers_a_caller_supplied_error_type() -> None:
    """`error_type` is a courtesy the processor adds to replace the correlator
    lost when it drops `exc_info` — but if a caller already set its own
    `error_type` (a different meaning at some future call site), that value must
    win, not be silently overwritten."""
    exc = RuntimeError("every alert channel failed")
    mark_already_logged(exc)
    out = _downgrade_already_logged_exceptions(
        None, "exception", {"event": "x", "exc_info": exc, "error_type": "custom"}
    )
    assert out["error_type"] == "custom"


def test_downgrades_a_marked_exception_from_any_caller(capsys: pytest.CaptureFixture[str]) -> None:
    """The actual regression test for #1261: a THIRD caller — neither
    `alerting/dispatch.py::publish_connection_health` nor
    `main.py::_poll_staleness_loop`, and carrying no `was_already_logged` check of
    its own — gets the exact same downgrade, because the processor is wired into
    the REAL `configure_logging()` chain rather than opted into per call site.
    The old per-caller-check architecture could not have passed this test without
    editing this synthetic call site too; this one needs zero changes at the call
    site to pick up the fix.

    Goes through the full production pipeline (`configure_logging()` + stdout
    JSON), not `structlog.testing.capture_logs()` — that helper derives its own
    `log_level` straight from the invoked method name and cannot observe a
    processor's level rewrite (see the #1261 note atop the caller-side tests in
    `test_main.py` / `test_connection_health.py`), so it cannot prove this."""
    configure_logging()
    log = structlog.get_logger("a_totally_new_caller_nobody_special_cased")

    exc = RuntimeError("every alert channel failed")
    mark_already_logged(exc)
    try:
        raise exc
    except RuntimeError:
        log.exception("third_caller_failed")

    line = capsys.readouterr().out
    record = json.loads(line)
    assert record["level"] == "warning"
    assert "exception" not in record  # no rendered traceback
    assert record["error_type"] == "RuntimeError"


def test_an_unmarked_exception_from_the_same_new_caller_still_gets_a_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The other half of the same proof: the synthetic third caller does NOT go
    blind to a genuinely new bug just because the processor exists."""
    configure_logging()
    log = structlog.get_logger("a_totally_new_caller_nobody_special_cased")

    try:
        raise RuntimeError("a bug nobody has seen before")
    except RuntimeError:
        log.exception("third_caller_failed_again")

    line = capsys.readouterr().out
    record = json.loads(line)
    assert record["level"] == "error"
    assert "exception" in record  # full traceback rendered, as normal
