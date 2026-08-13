"""Tests for the request_id middleware: validation + structured-log emission.

Per 2026-05-28 security audit + observability work (#50, #51).
"""

import asyncio
import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from structlog.testing import capture_logs
from structlog.typing import EventDict

from backend.app import main as main_module
from backend.app.alerting.base import mark_already_logged
from backend.app.core.config import Settings
from backend.app.core.logging import _downgrade_already_logged_exceptions
from backend.app.main import REQUEST_ID_HEADER, app, docs_kwargs


@pytest.fixture
def client() -> Iterator[TestClient]:
    # FastAPI lifespan runs configure_logging(); avoid re-running it for tests
    # by entering the context manually under TestClient.
    with TestClient(app) as c:
        yield c


# ───────────────────────── X-Request-ID validation ─────────────────────────


def test_request_id_generated_when_caller_omits(client: TestClient) -> None:
    response = client.get("/healthz")
    rid = response.headers.get(REQUEST_ID_HEADER)
    assert rid is not None
    # uuid4().hex is 32 lowercase hex chars
    assert len(rid) == 32
    assert all(c in "0123456789abcdef" for c in rid)


def test_request_id_echoed_when_valid(client: TestClient) -> None:
    response = client.get("/healthz", headers={REQUEST_ID_HEADER: "trace-abc.123_XYZ"})
    assert response.headers[REQUEST_ID_HEADER] == "trace-abc.123_XYZ"


def test_request_id_replaced_when_too_long(client: TestClient) -> None:
    """Caller-supplied IDs over 64 chars are rejected (security audit #2)."""
    too_long = "a" * 65
    response = client.get("/healthz", headers={REQUEST_ID_HEADER: too_long})
    echoed = response.headers[REQUEST_ID_HEADER]
    assert echoed != too_long
    assert len(echoed) == 32  # fresh uuid


def test_request_id_replaced_on_bad_chars(client: TestClient) -> None:
    """Caller-supplied IDs containing whitespace or JSON-control chars are rejected."""
    for bad in ['inject"quote', "with space", "tab\there", "semi;colon"]:
        response = client.get("/healthz", headers={REQUEST_ID_HEADER: bad})
        echoed = response.headers[REQUEST_ID_HEADER]
        assert echoed != bad
        assert len(echoed) == 32


def test_request_id_replaced_on_empty(client: TestClient) -> None:
    response = client.get("/healthz", headers={REQUEST_ID_HEADER: ""})
    echoed = response.headers[REQUEST_ID_HEADER]
    assert len(echoed) == 32


# ───────────────────────── per-request structured log ─────────────────────────


def _request_events_from_caplog(records: list[logging.LogRecord]) -> list[dict[str, object]]:
    """Pick request-event records that structlog routed through stdlib logging.

    structlog's stdlib bridge emits records whose `msg` is the rendered dict
    (post-processors) — args slot carries the original event_dict.
    """
    out: list[dict[str, object]] = []
    for rec in records:
        # The structlog ProcessorFormatter wraps the original event_dict on rec.msg
        # (after processors run). We pull from rec.__dict__ to get the structured fields.
        evt = getattr(rec, "_record", None) or rec.__dict__.get("event_dict")
        if evt is None and isinstance(rec.msg, dict):
            evt = rec.msg
        if not isinstance(evt, dict):
            continue
        if evt.get("event") == "request":
            out.append(evt)
    return out


def test_per_request_log_emitted_on_success(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """#51: every successful request emits one event=request structlog entry."""
    caplog.set_level(logging.INFO, logger="backend.app.main")
    client.get("/healthz", headers={REQUEST_ID_HEADER: "trace-1"})
    events = _request_events_from_caplog(caplog.records)
    assert len(events) == 1
    evt = events[0]
    assert evt["method"] == "GET"
    assert evt["path"] == "/healthz"
    assert evt["status"] == 200
    assert isinstance(evt["duration_ms"], int | float)
    assert evt["duration_ms"] >= 0
    assert evt["request_id"] == "trace-1"


def test_per_request_log_uses_generated_request_id_when_invalid(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """Invalid X-Request-ID → generated UUID propagates into the request log."""
    caplog.set_level(logging.INFO, logger="backend.app.main")
    client.get("/healthz", headers={REQUEST_ID_HEADER: "with space"})
    events = _request_events_from_caplog(caplog.records)
    assert len(events) == 1
    rid = events[0]["request_id"]
    assert isinstance(rid, str)
    assert len(rid) == 32  # uuid4().hex, not the rejected "with space"


# ───────────────────────── prod-docs gate (#170) ─────────────────────────


def test_docs_enabled_in_dev_and_staging() -> None:
    for env in ("dev", "staging"):
        kw = docs_kwargs(Settings(_env_file=None, environment=env))
        assert kw == {
            "docs_url": "/docs",
            "redoc_url": "/redoc",
            "openapi_url": "/openapi.json",
        }


def test_docs_disabled_in_prod() -> None:
    kw = docs_kwargs(Settings(_env_file=None, environment="prod"))
    assert kw == {"docs_url": None, "redoc_url": None, "openapi_url": None}


def test_openapi_schema_served_in_test_env(client: TestClient) -> None:
    """The test env is non-prod, so the wired app exposes the schema + docs UI."""
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    resp = client.get("/docs")
    assert resp.status_code == 200


def test_every_api_endpoint_has_summary_and_tags() -> None:
    """Swagger-completeness guardrail (W7 hardening): every /api/* operation
    carries a `summary` and at least one `tag`, so Swagger/ReDoc stay navigable
    and self-describing. Fails loudly if a new endpoint omits them."""
    schema = app.openapi()
    http_methods = {"get", "post", "put", "patch", "delete"}
    missing: list[str] = []
    for path, operations in schema["paths"].items():
        if not path.startswith("/api/"):
            continue  # /healthz and the mounted /mcp app are out of scope
        for method, op in operations.items():
            if method not in http_methods:
                continue
            if not op.get("summary"):
                missing.append(f"{method.upper()} {path}: missing summary")
            if not op.get("tags"):
                missing.append(f"{method.upper()} {path}: missing tags")
    assert not missing, "endpoints missing Swagger metadata:\n" + "\n".join(missing)


# ───────────────── readiness vs liveness (#748) ─────────────────
#
# The 2026-07-10 incident: a queued ACCESS EXCLUSIVE lock blocked every reader for
# ~25 minutes while `/healthz` stayed green the whole time, because it only proved
# the process was running. A health signal that cannot go red during a total read
# degradation is not a health signal.


def test_healthz_stays_up_when_the_database_is_down(client: TestClient) -> None:
    """Liveness must NOT depend on the database.

    A liveness probe that fails on a DB blip gets the container killed and
    restarted, which cannot fix a database and turns a degradation into an outage.
    """
    from backend.app.db.session import get_db

    def _broken_db() -> object:
        raise RuntimeError("database unreachable")

    app.dependency_overrides[get_db] = _broken_db
    try:
        response = client.get("/healthz")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200


def test_readyz_goes_red_when_the_database_cannot_be_read(client: TestClient) -> None:
    """The signal the incident lacked. 503, not 500: this is "not ready to serve",
    which is what a load balancer and an alert need to distinguish.

    The fake fails only on the actual READ, not on the timeout setup — an earlier
    version raised on the first `execute` and so passed even with the `SELECT 1`
    deleted, proving 503-on-error without proving the probe touches the database
    at all. Caught by mutation.
    """
    from backend.app.db.session import get_db

    statements: list[str] = []

    class _HangingSession:
        def execute(self, statement: object, *_a: object, **_k: object) -> object:
            text_ = str(statement)
            statements.append(text_)
            if "SELECT 1" in text_:
                raise RuntimeError("canceling statement due to statement timeout")
            return None

    app.dependency_overrides[get_db] = lambda: _HangingSession()
    try:
        response = client.get("/readyz")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 503
    assert any("SELECT 1" in s for s in statements), "the probe must actually read"
    assert any("statement_timeout" in s for s in statements), "and must bound itself"


def test_readyz_never_echoes_the_database_error(client: TestClient) -> None:
    """This route is UNAUTHENTICATED, and a DSN in a driver error carries a
    password (#536). The reason belongs in the logs, not the response body."""
    from backend.app.db.session import get_db

    secret_ish = "could not connect to postgresql://dataq:hunter2@db:5432/dataq"

    class _LeakySession:
        def execute(self, statement: object, *_a: object, **_k: object) -> object:
            if "SELECT 1" in str(statement):
                raise RuntimeError(secret_ish)
            return None

    app.dependency_overrides[get_db] = lambda: _LeakySession()
    try:
        response = client.get("/readyz")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert "hunter2" not in response.text
    assert "postgresql://" not in response.text


# ───────────── poll-staleness watchdog: no duplicate traceback (#1226/#1261) ────────
#
# When every alert channel fails, `CompositePublisher._fan_out_delivered_first`
# already logs a full traceback per failing channel before re-raising the last
# one. `_poll_staleness_loop`'s own `except Exception: logger.exception(...)` used
# to log that SAME last-channel traceback a second time. #1226 fixed it with a
# per-caller `was_already_logged` check; #1261 moved the check into the structlog
# processor chain (`_downgrade_already_logged_exceptions`, core/logging.py) so
# `_poll_staleness_loop` is back to an unconditional `logger.exception(...)` and
# the downgrade happens for ANY caller, not just this one.
#
# `structlog.testing.capture_logs()` clears the app's configured processor list
# for its duration (that's the point — isolate the test from unrelated chain
# config), so it must be told about this specific processor via its `processors=`
# param to exercise it at all. It also derives its OWN `log_level` key straight
# from the bound-logger method name that was called (`exception` → `error`),
# which no downstream processor can influence — so the processor's rewrite is
# only observable on the `level` key it actually writes, not `log_level`. See
# `test_downgrades_a_marked_exception_from_any_caller` in
# `backend/tests/core/test_logging.py` for the real end-to-end proof (the full
# `configure_logging()` pipeline, no `capture_logs()` involved).


async def _run_one_tick(monkeypatch: pytest.MonkeyPatch, tick_error: Exception) -> list[EventDict]:
    """Drive exactly one iteration of `_poll_staleness_loop` whose tick raises
    ``tick_error``, and return the structlog events captured during it.

    The tick itself sets `stop` before raising, so the loop's own `while not
    stop.is_set()` guard exits after logging — no timing-dependent second tick.
    """
    stop = asyncio.Event()

    def _boom() -> str:
        stop.set()
        raise tick_error

    monkeypatch.setattr(main_module, "_poll_staleness_tick", _boom)

    with capture_logs(processors=[_downgrade_already_logged_exceptions]) as logs:
        await main_module._poll_staleness_loop(stop, interval_s=60)
    return list(logs)


async def test_poll_staleness_loop_downgrades_an_error_the_composite_already_logged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exc = RuntimeError("every alert channel failed")
    mark_already_logged(exc)

    events = await _run_one_tick(monkeypatch, exc)

    ticks = [e for e in events if e["event"] == "poll_staleness_tick_failed"]
    assert len(ticks) == 1
    assert ticks[0]["level"] == "warning"
    assert "exc_info" not in ticks[0]
    # No exc_info and no per-tick correlation id in this background loop, so
    # error_type is the only thing left to match this line back to the composite's
    # own per-channel log (review finding on #1260; now restored by the processor
    # itself, #1261, for any caller it downgrades — not hand-added here).
    assert ticks[0]["error_type"] == "RuntimeError"


async def test_poll_staleness_loop_still_logs_a_full_traceback_for_anything_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The downgrade must be narrow: an ordinary, unmarked exception (a DB error,
    a bug elsewhere in the tick) is NOT the composite's already-logged error, so it
    must still surface with a full traceback — the fix must not go blind to new
    failures in this watchdog, which exists precisely to catch a dead worker."""
    events = await _run_one_tick(monkeypatch, RuntimeError("db connection reset"))

    ticks = [e for e in events if e["event"] == "poll_staleness_tick_failed"]
    assert len(ticks) == 1
    assert ticks[0]["log_level"] == "error"
    assert ticks[0].get("exc_info")
