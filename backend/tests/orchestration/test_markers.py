"""The shared `triggered_by` ↔ pipeline-run correlation (#1728)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.app.db.models import Connection, PipelineRun, Run, Suite, User
from backend.app.orchestration import markers


def _world(db_session: Any) -> tuple[Suite, Connection]:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:6]}@example.com")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"dbt-{uuid.uuid4().hex[:6]}",
        type="dbt",
        env="dev",
        config={},
        secret_ref="kv-x",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(name="s", connection_id=conn.id, created_by=owner.id, target={"table": "T"})
    db_session.add(suite)
    db_session.flush()
    return suite, conn


def _pipeline_run(db_session: Any, conn: Connection, *, pipeline: str, run_id: str) -> PipelineRun:
    pr = PipelineRun(
        provider="dbt",
        connection_id=conn.id,
        provider_run_id=run_id,
        pipeline_or_dag_id=pipeline,
        env="dev",
        status="succeeded",
        started_at=datetime.now(UTC),
    )
    db_session.add(pr)
    db_session.flush()
    return pr


def test_unique_marker_resolves_to_its_runs_newest_first(db_session: Any) -> None:
    # One pipeline run can trigger several suites (one per binding); per suite the
    # marker is unique (`uq_runs_suite_triggered_by`), so the second run is another suite's.
    suite, conn = _world(db_session)
    other_suite = Suite(name="s2", connection_id=conn.id, created_by=suite.created_by, target={})
    db_session.add(other_suite)
    db_session.flush()
    pr = _pipeline_run(db_session, conn, pipeline="nightly", run_id="run-7")
    now = datetime.now(UTC)
    older = Run(
        suite_id=suite.id,
        status="succeeded",
        triggered_by="dbt:nightly:run-7",
        created_at=now - timedelta(minutes=1),
    )
    db_session.add(older)
    db_session.flush()
    newer = Run(
        suite_id=other_suite.id, status="failed", triggered_by="dbt:nightly:run-7", created_at=now
    )
    db_session.add(newer)
    db_session.commit()

    assert markers.triggered_runs(db_session, [pr]).by_pipeline_run == {pr.id: [newer.id, older.id]}
    assert markers.unambiguous_pipeline_run(db_session, "dbt:nightly:run-7") is pr
    assert markers.ambiguous_markers(db_session, ["dbt:nightly:run-7"]) == set()


def test_colliding_markers_fail_closed_for_every_reader(db_session: Any) -> None:
    """("nightly:etl", "run-1") and ("nightly", "etl:run-1") both reconstruct to
    "dbt:nightly:etl:run-1"; the DQ run exists but belongs to neither for sure.
    """
    suite, conn = _world(db_session)
    a = _pipeline_run(db_session, conn, pipeline="nightly:etl", run_id="run-1")
    b = _pipeline_run(db_session, conn, pipeline="nightly", run_id="etl:run-1")
    clean = _pipeline_run(db_session, conn, pipeline="daily", run_id="run-2")
    db_session.add(Run(suite_id=suite.id, status="succeeded", triggered_by="dbt:nightly:etl:run-1"))
    dq = Run(suite_id=suite.id, status="succeeded", triggered_by="dbt:daily:run-2")
    db_session.add(dq)
    db_session.commit()

    assert markers.ambiguous_markers(db_session, ["dbt:nightly:etl:run-1", "dbt:daily:run-2"]) == {
        "dbt:nightly:etl:run-1"
    }
    correlated = markers.triggered_runs(db_session, [a, b, clean])
    assert correlated.by_pipeline_run == {a.id: [], b.id: [], clean.id: [dq.id]}
    assert correlated.ambiguous_markers == {"dbt:nightly:etl:run-1"}
    assert correlated.is_ambiguous(a) and correlated.is_ambiguous(b)
    assert not correlated.is_ambiguous(clean)
    assert markers.unambiguous_pipeline_run(db_session, "dbt:nightly:etl:run-1") is None
    assert len(markers.pipeline_runs_for_marker(db_session, "dbt:nightly:etl:run-1")) == 2


def test_marker_without_provider_separator_matches_nothing(db_session: Any) -> None:
    assert markers.pipeline_runs_for_marker(db_session, "manual") == []
    assert markers.triggered_runs(db_session, []).by_pipeline_run == {}
