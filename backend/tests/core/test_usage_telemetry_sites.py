"""Each server-side emit site fires its event, with vocabulary values only (ADR 0050)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from backend.app.core import usage_telemetry
from backend.app.db.models import Result, Run
from backend.app.services import check_service, run_dispatch
from backend.app.services import telemetry_settings_service as svc
from backend.tests.support.llm_helpers import admin_user, make_sql_suite

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def logs(db_session: Any) -> Iterator[Any]:
    from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    usage_telemetry.reset()
    exporter = InMemoryLogRecordExporter()
    usage_telemetry.configure(
        "test", log_exporters=[exporter], metric_readers=[InMemoryMetricReader()]
    )
    svc.set_enabled(db_session, enabled=True, actor=admin_user(db_session, prefix="tel"))
    db_session.commit()
    # Paths with no session of their own (MCP) read the cached opt-in; prime it from this
    # transaction, which a fresh connection cannot see.
    assert svc.opt_in(db_session) is True
    exporter.clear()
    yield exporter
    usage_telemetry.reset()
    svc.invalidate()


def _events(exporter: Any) -> list[tuple[str, dict[str, Any]]]:
    return [
        (r.log_record.event_name, dict(r.log_record.attributes))
        for r in exporter.get_finished_logs()
    ]


def test_check_created_carries_kind_engine_datasource_and_origin(
    db_session: Any, logs: Any
) -> None:
    owner = admin_user(db_session, prefix="own")
    suite = make_sql_suite(db_session, owner, conn_type="postgres")
    check_service.create_check(
        db_session,
        suite_id=suite.id,
        name="orders are fresh",
        kind="freshness",
        expectation_type="monitor:freshness",
        config={"column": "updated_at"},
        warn_threshold=None,
        fail_threshold=Decimal(24),
        critical_threshold=None,
    )
    assert _events(logs) == [
        (
            "dataq.usage.check_created",
            {
                "event": "check_created",
                "kind": "freshness",
                "engine": "gx",
                "datasource_type": "postgres",
                "origin": "user",
            },
        )
    ]


def test_suite_run_triggered_reports_the_trigger_kind_not_the_marker(
    db_session: Any, logs: Any
) -> None:
    owner = admin_user(db_session, prefix="own")
    suite = make_sql_suite(db_session, owner)
    run = run_dispatch.new_queued_run(suite, triggered_by=f"manual:{owner.id}")
    db_session.add(run)
    db_session.commit()
    assert run_dispatch.dispatch_or_fail(db_session, run)
    assert _events(logs) == [
        ("dataq.usage.suite_run_triggered", {"event": "suite_run_triggered", "trigger": "manual"})
    ]


def test_run_completed_carries_outcome_datasource_and_counts(db_session: Any, logs: Any) -> None:
    from backend.app.worker import tasks

    owner = admin_user(db_session, prefix="own")
    suite = make_sql_suite(db_session, owner, conn_type="trino")
    started = datetime.now(UTC)
    run = Run(
        suite_id=suite.id,
        status="succeeded",
        triggered_by="schedule:x",
        started_at=started,
        finished_at=started + timedelta(milliseconds=1500),
    )
    db_session.add(run)
    db_session.flush()
    check = check_service.create_check(
        db_session,
        suite_id=suite.id,
        name="rows",
        kind="volume",
        expectation_type="monitor:volume",
        config={"min_rows": 1, "max_rows": 10},
        warn_threshold=None,
        fail_threshold=None,
        critical_threshold=None,
    )
    db_session.add(Result(run_id=run.id, check_id=check.id, status="pass"))
    db_session.commit()
    logs.clear()

    tasks._usage_run_completed(db_session, run_id=run.id)

    assert _events(logs) == [
        (
            "dataq.usage.run_completed",
            {
                "event": "run_completed",
                "outcome": "succeeded",
                "datasource_type": "trino",
                "checks": 1,
                "duration_ms": 1500,
            },
        )
    ]


def test_run_completed_skips_a_missing_run(db_session: Any, logs: Any) -> None:
    from backend.app.worker import tasks

    tasks._usage_run_completed(db_session, run_id=uuid.uuid4())
    assert _events(logs) == []


def test_llm_feature_used_skips_the_admin_ping() -> None:
    assert "ping" not in usage_telemetry.LLM_FEATURES
    assert set(usage_telemetry.LLM_FEATURES) == {
        "sql_generation",
        "check_suggestion",
        "rca_narrative",
    }


def test_mcp_tool_called_names_the_tool_and_outcome(logs: Any) -> None:
    from backend.app.mcp import server

    server.install_usage_telemetry()
    assert "list_suites" in usage_telemetry.MCP_TOOL_NAMES.names
    middleware = server._UsageTelemetryMiddleware()

    class _Ctx:
        message = type("M", (), {"name": "list_suites"})()

    async def _ok(_ctx: Any) -> str:
        return "ok"

    async def _boom(_ctx: Any) -> str:
        raise RuntimeError("tool failed")

    assert asyncio.run(middleware.on_call_tool(_Ctx(), _ok)) == "ok"
    with pytest.raises(RuntimeError):
        asyncio.run(middleware.on_call_tool(_Ctx(), _boom))
    assert _events(logs) == [
        (
            "dataq.usage.mcp_tool_called",
            {"event": "mcp_tool_called", "tool": "list_suites", "outcome": "ok"},
        ),
        (
            "dataq.usage.mcp_tool_called",
            {"event": "mcp_tool_called", "tool": "list_suites", "outcome": "error"},
        ),
    ]


def test_run_completed_does_nothing_while_telemetry_is_off(db_session: Any) -> None:
    from backend.app.worker import tasks

    usage_telemetry.reset()

    class _NoQueries:
        def get(self, *_: Any) -> None:
            raise AssertionError("queried with telemetry off")

    tasks._usage_run_completed(_NoQueries(), run_id=uuid.uuid4())  # type: ignore[arg-type]


def test_run_completed_never_raises_into_the_task(logs: Any) -> None:
    from backend.app.worker import tasks

    class _Broken:
        def get(self, *_: Any) -> None:
            raise RuntimeError("db gone")

        def rollback(self) -> None:
            return None

    tasks._usage_run_completed(_Broken(), run_id=uuid.uuid4())  # type: ignore[arg-type]
    assert _events(logs) == []


def test_run_completed_omits_an_unknown_duration(db_session: Any, logs: Any) -> None:
    from backend.app.worker import tasks

    owner = admin_user(db_session, prefix="own")
    suite = make_sql_suite(db_session, owner, conn_type="postgres")
    run = Run(suite_id=suite.id, status="failed", triggered_by="manual:x")
    db_session.add(run)
    db_session.commit()

    tasks._usage_run_completed(db_session, run_id=run.id)

    ((_, attributes),) = _events(logs)
    assert "duration_ms" not in attributes
    assert attributes["outcome"] == "failed" and attributes["checks"] == 0


def test_an_imported_suite_counts_each_check(db_session: Any, logs: Any) -> None:
    from backend.app.services import suite_io_service

    owner = admin_user(db_session, prefix="own")
    suite = make_sql_suite(db_session, owner, conn_type="postgres")
    logs.clear()
    suite_io_service.import_suite(
        db_session,
        version=1,
        name="imported",
        description=None,
        checks=[
            {
                "name": f"rows {n}",
                "kind": "volume",
                "expectation_type": "monitor:volume",
                "config": {"min_rows": 1, "max_rows": 10},
                "warn_threshold": None,
                "fail_threshold": None,
                "critical_threshold": None,
            }
            for n in range(2)
        ],
        connection_id=suite.connection_id,
        created_by=owner.id,
    )
    assert [a["origin"] for _, a in _events(logs)] == ["import", "import"]


def test_a_cancelled_tool_call_is_not_counted_as_ok(logs: Any) -> None:
    from backend.app.mcp import server

    usage_telemetry.register_mcp_tool_names(["list_suites"])

    class _Ctx:
        message = type("M", (), {"name": "list_suites"})()

    async def _cancelled(_ctx: Any) -> str:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(server._UsageTelemetryMiddleware().on_call_tool(_Ctx(), _cancelled))
    assert [a["outcome"] for _, a in _events(logs)] == ["cancelled"]
