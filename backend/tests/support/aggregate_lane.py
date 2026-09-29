"""The shared dataset and assertions every datasource lane uses to exercise the `aggregate`
monitor (#1602) against a real engine: the lane creates a table holding `AMOUNTS` in a numeric
column (and a column that is NULL on every row) plus an empty table, then calls these.

The expected values are computed here from the Python list, never from the engine, so a driver
that hands back an unexpected type or a dialect that spells an aggregate differently fails.
"""

from __future__ import annotations

import statistics
from decimal import Decimal
from typing import Any

import pytest

from backend.app.datasources.base import MonitorSpec
from backend.app.datasources.monitors import AGGREGATES

# Two decimal places, a NULL and a negative: exact in NUMERIC(9,2) on every engine. Six values
# whose mean (10.79333…) does not terminate, so an engine that keeps AVG at a fixed decimal
# scale is caught rounding.
AMOUNTS: list[Decimal | None] = [
    Decimal("10.50"),
    None,
    Decimal("20.50"),
    Decimal("30.01"),
    Decimal("-4.25"),
    Decimal("7.00"),
    Decimal("1.00"),
]
_VALUES = [float(v) for v in AMOUNTS if v is not None]

EXPECTED: dict[str, float] = {
    "mean": statistics.fmean(_VALUES),
    "median": statistics.median(_VALUES),
    "sum": sum(_VALUES),
    "stdev": statistics.stdev(_VALUES),
    "min": min(_VALUES),
    "max": max(_VALUES),
}


def aggregate_spec(aggregate: str, column: str, **bounds: float) -> MonitorSpec:
    config: dict[str, Any] = {"aggregate": aggregate, "column": column}
    config.update(bounds or {"min_value": -1000.0, "max_value": 1000.0})
    return MonitorSpec(kind="aggregate", config=config)


def assert_aggregates(
    runner: Any,
    *,
    table: str,
    schema: str | None,
    column: str,
    null_column: str,
    empty_table: str,
    exact_median: bool = True,
) -> None:
    """Every aggregate over the data table, the all-NULL column and the empty table."""
    aggregates = [a for a in AGGREGATES if exact_median or a != "median"]
    outcomes = runner.run_monitors(
        table=table, schema=schema, monitors=[aggregate_spec(a, column) for a in aggregates]
    )
    for aggregate, outcome in zip(aggregates, outcomes, strict=True):
        assert outcome.errored is False, (aggregate, outcome.error_message)
        assert outcome.metric_value == pytest.approx(EXPECTED[aggregate], rel=1e-9), aggregate
        assert outcome.severity == "pass", aggregate
    # Two-sided: too high fails, too low goes critical.
    high, low = runner.run_monitors(
        table=table,
        schema=schema,
        monitors=[
            aggregate_spec("max", column, max_value=25.0),
            aggregate_spec("min", column, critical_min=0.0, min_value=1.0),
        ],
    )
    assert (high.severity, high.observed_value["breached_bound"]) == ("fail", "max_value")
    assert (low.severity, low.observed_value["breached_bound"]) == ("critical", "critical_min")
    for where, target, col in (
        ("all-NULL column", table, null_column),
        ("empty table", empty_table, column),
    ):
        nulls = runner.run_monitors(
            table=target, schema=schema, monitors=[aggregate_spec(a, col) for a in aggregates]
        )
        for aggregate, outcome in zip(aggregates, nulls, strict=True):
            assert outcome.errored is True, (where, aggregate, outcome)
            assert outcome.metric_value is None, (where, aggregate)
            assert "is NULL" in (outcome.error_message or ""), (where, aggregate)


def assert_run_path_and_dry_run(
    db_session: Any,
    *,
    conn_type: str,
    config: dict[str, Any],
    secret_ref: str | None,
    secret_store: Any,
    target: dict[str, Any],
    column: str,
) -> None:
    """Author two aggregate checks through `check_service`, run them through `execute_run` (the
    worker's own persistence) and preview one through `dry_run_check` — both on the real engine.
    """
    import uuid

    from sqlalchemy import select

    from backend.app.datasources.registry import build_check_runner
    from backend.app.db.models import Connection, Result, Run, Suite, User
    from backend.app.services import (
        check_service,
        dryrun_service,
        run_service,
        run_target,
    )

    owner = User(aad_object_id=uuid.uuid4().hex, email=f"agg-{uuid.uuid4().hex[:6]}@ex")
    db_session.add(owner)
    db_session.flush()
    connection = Connection(
        name=f"agg-{uuid.uuid4().hex[:6]}",
        type=conn_type,
        env="dev",
        config=config,
        secret_ref=secret_ref,
        created_by=owner.id,
    )
    db_session.add(connection)
    db_session.flush()
    suite = Suite(name="agg", connection_id=connection.id, created_by=owner.id, target=target)
    db_session.add(suite)
    db_session.commit()
    checks = [
        check_service.create_check(
            db_session,
            suite_id=suite.id,
            name=name,
            kind="aggregate",
            expectation_type="monitor:aggregate",
            config={"aggregate": aggregate, "column": column, **bounds},
            warn_threshold=None,
            fail_threshold=None,
            critical_threshold=None,
            actor_id=owner.id,
        )
        for name, aggregate, bounds in (
            ("mean in range", "mean", {"min_value": 0.0, "max_value": 100.0}),
            ("max too high", "max", {"warn_max": 20.0, "max_value": 25.0}),
        )
    ]
    run = Run(suite_id=suite.id, status="queued")
    db_session.add(run)
    db_session.commit()
    resolved = run_target.resolve_target(conn_type, target)
    runner = build_check_runner(
        conn_type=conn_type,
        config=config,
        secret_ref=secret_ref,
        secret_store=secret_store,
        catalog=resolved.catalog,
    )
    table = run_target.materialize_path(
        conn_type, config, resolved, secret_ref=secret_ref, secret_store=secret_store
    )
    try:
        run_service.execute_run(
            db_session,
            run=run,
            checks=checks,
            runner=runner,
            table=table,
            schema=resolved.schema,
        )
    finally:
        close = getattr(runner, "close", None)
        if callable(close):
            close()
    assert run.status == "succeeded"
    by_check = {
        r.check_id: r for r in db_session.scalars(select(Result).where(Result.run_id == run.id))
    }
    mean, high = by_check[checks[0].id], by_check[checks[1].id]
    assert (mean.status, high.status) == ("pass", "fail")
    # The scalar lives in the NUMERIC column, where trends and baselines aggregate it.
    assert float(mean.metric_value) == pytest.approx(EXPECTED["mean"], rel=1e-9)
    assert float(high.metric_value) == pytest.approx(EXPECTED["max"], rel=1e-9)

    preview = dryrun_service.dry_run_check(
        connection,
        session=db_session,
        kind="aggregate",
        expectation_type="monitor:aggregate",
        config={"aggregate": "sum", "column": column, "max_value": 10.0},
        warn_threshold=None,
        fail_threshold=None,
        critical_threshold=None,
        target=target,
        secret_store=secret_store,
    )
    assert preview.status == "fail"
    assert preview.metric_value is not None
    assert float(preview.metric_value) == pytest.approx(EXPECTED["sum"], rel=1e-9)
