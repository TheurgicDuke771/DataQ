"""compute_pipeline_cadence (#1648): inter-run gaps grounding a freshness suggestion."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.app.db.models import Connection, PipelineRun, User
from backend.app.services.orchestration_service import PipelineCadence, compute_pipeline_cadence

NOW = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)


def _connection(db_session: Any) -> Connection:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@example.com")
    db_session.add(user)
    db_session.flush()
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:6]}", type="airflow", env="dev", config={}, created_by=user.id
    )
    db_session.add(conn)
    db_session.commit()
    return conn


def _run(db_session: Any, conn: Connection, *, hours_ago: float, status: str = "succeeded") -> None:
    started = NOW - timedelta(hours=hours_ago)
    db_session.add(
        PipelineRun(
            provider="airflow",
            connection_id=conn.id,
            provider_run_id=f"run-{uuid.uuid4().hex[:8]}",
            pipeline_or_dag_id="load_orders",
            env="dev",
            status=status,
            started_at=started,
            finished_at=started + timedelta(minutes=5),
            created_at=started,
        )
    )


def _cadence(db_session: Any) -> PipelineCadence:
    return compute_pipeline_cadence(
        db_session, provider="airflow", pipeline_or_dag_id="load_orders", env="dev"
    )


def test_insufficient_history_below_the_minimum_run_count(db_session: Any) -> None:
    conn = _connection(db_session)
    _run(db_session, conn, hours_ago=2)
    _run(db_session, conn, hours_ago=1)  # only 2 succeeded runs — below the min
    db_session.commit()

    cadence = _cadence(db_session)

    assert cadence.insufficient_history is True
    assert cadence.sample_count == 2
    assert cadence.median_gap_hours is None
    assert cadence.suggested_fail_threshold_hours is None


def test_regular_cadence_computes_median_and_a_margined_default(db_session: Any) -> None:
    conn = _connection(db_session)
    # Four runs, evenly spaced 6 hours apart.
    for hours_ago in (18, 12, 6, 0):
        _run(db_session, conn, hours_ago=hours_ago)
    db_session.commit()

    cadence = _cadence(db_session)

    assert cadence.insufficient_history is False
    assert cadence.sample_count == 4
    assert cadence.median_gap_hours == 6.0
    assert cadence.max_gap_hours == 6.0
    # Deterministic fallback: margined above the worst observed gap, not tight to it.
    assert cadence.suggested_fail_threshold_hours == 7.5


def test_an_irregular_gap_widens_the_max_but_not_the_median(db_session: Any) -> None:
    conn = _connection(db_session)
    # Mostly 4h apart, one outlier 20h gap.
    for hours_ago in (28, 8, 4, 0):
        _run(db_session, conn, hours_ago=hours_ago)
    db_session.commit()

    cadence = _cadence(db_session)

    assert cadence.median_gap_hours == 4.0
    assert cadence.max_gap_hours == 20.0
    assert cadence.suggested_fail_threshold_hours == 25.0


def test_failed_runs_are_excluded_from_the_cadence(db_session: Any) -> None:
    """A failed run produced no new data — it must not count as an arrival."""
    conn = _connection(db_session)
    for hours_ago in (12, 8, 4, 0):
        _run(db_session, conn, hours_ago=hours_ago, status="succeeded")
    _run(db_session, conn, hours_ago=2, status="failed")  # would shrink the gap if counted
    db_session.commit()

    cadence = _cadence(db_session)

    assert cadence.sample_count == 4
    assert cadence.median_gap_hours == 4.0


def test_a_different_pipeline_or_env_does_not_contaminate_the_cadence(db_session: Any) -> None:
    conn = _connection(db_session)
    for hours_ago in (12, 8, 4, 0):
        _run(db_session, conn, hours_ago=hours_ago)
    # A same-connection run for a DIFFERENT dag, and one in a different env.
    db_session.add(
        PipelineRun(
            provider="airflow",
            connection_id=conn.id,
            provider_run_id="other-dag",
            pipeline_or_dag_id="load_payments",
            env="dev",
            status="succeeded",
            started_at=NOW,
            created_at=NOW,
        )
    )
    db_session.add(
        PipelineRun(
            provider="airflow",
            connection_id=conn.id,
            provider_run_id="other-env",
            pipeline_or_dag_id="load_orders",
            env="qa",
            status="succeeded",
            started_at=NOW,
            created_at=NOW,
        )
    )
    db_session.commit()

    cadence = _cadence(db_session)

    assert cadence.sample_count == 4


def test_no_history_at_all(db_session: Any) -> None:
    cadence = compute_pipeline_cadence(
        db_session, provider="airflow", pipeline_or_dag_id="never_run", env="dev"
    )
    assert cadence.insufficient_history is True
    assert cadence.sample_count == 0
