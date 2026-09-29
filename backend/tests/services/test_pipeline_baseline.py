"""Pipeline duration + overdue baselines (#1653)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from backend.app.alerting.base import AlertUndeliverableError, PipelineBaselineReport
from backend.app.db.models import (
    Connection,
    PipelineRun,
    Suite,
    TriggerBinding,
    User,
    WorkspaceHealth,
)
from backend.app.services import pipeline_baseline as pb

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


class _Publisher:
    def __init__(self, undeliverable: bool = False) -> None:
        self.reports: list[PipelineBaselineReport] = []
        self.undeliverable = undeliverable

    def publish_workspace_signal(self, session: Any, report: PipelineBaselineReport) -> bool:
        if self.undeliverable:
            raise AlertUndeliverableError("no alert channel is configured")
        self.reports.append(report)
        return True


@pytest.fixture
def publisher(monkeypatch: pytest.MonkeyPatch) -> _Publisher:
    pub = _Publisher()
    monkeypatch.setattr(pb, "get_health_publisher", lambda: pub)
    return pub


@pytest.fixture
def world(db_session: Any) -> dict[str, Any]:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex[:8]}@x.io")
    db_session.add(owner)
    db_session.flush()
    airflow = Connection(
        name=f"af-{uuid.uuid4().hex[:6]}",
        type="airflow",
        env="dev",
        config={"base_url": "https://airflow.example.com"},
        secret_ref="kv-af",
        created_by=owner.id,
    )
    warehouse = Connection(
        name=f"pg-{uuid.uuid4().hex[:6]}",
        type="postgres",
        env="dev",
        config={"host": "h", "database": "d", "user": "u"},
        secret_ref="kv-pg",
        created_by=owner.id,
    )
    db_session.add_all([airflow, warehouse])
    db_session.flush()
    suite = Suite(name="bound", connection_id=warehouse.id, created_by=owner.id)
    db_session.add(suite)
    db_session.flush()
    db_session.add(
        TriggerBinding(
            provider="airflow", pipeline_or_dag_id="load_orders", env="dev", suite_id=suite.id
        )
    )
    db_session.commit()
    return {"conn": airflow}


def _run(
    db_session: Any,
    world: dict[str, Any],
    *,
    started: datetime,
    seconds: float,
    env: str = "dev",
    status: str = "succeeded",
) -> PipelineRun:
    run = PipelineRun(
        provider="airflow",
        connection_id=world["conn"].id,
        provider_run_id=uuid.uuid4().hex,
        pipeline_or_dag_id="load_orders",
        env=env,
        status=status,
        started_at=started,
        finished_at=started + timedelta(seconds=seconds),
    )
    db_session.add(run)
    db_session.commit()
    return run


def _daily(
    db_session: Any, world: dict[str, Any], durations: list[float], *, last: datetime
) -> list[PipelineRun]:
    n = len(durations)
    return [
        _run(db_session, world, started=last - timedelta(days=n - 1 - i), seconds=d)
        for i, d in enumerate(durations)
    ]


def test_too_little_history_reports_the_mean_but_no_score(
    db_session: Any, world: dict[str, Any]
) -> None:
    runs = _daily(db_session, world, [100, 110, 90, 500], last=NOW)
    score = pb.score_duration(db_session, runs[-1])
    assert score is not None
    assert score.points == 3 and score.insufficient_history
    assert score.mean_seconds == 100 and score.z_score is None
    assert score.delay_seconds == 400


def test_a_slow_run_scores_far_out_and_a_normal_one_does_not(
    db_session: Any, world: dict[str, Any]
) -> None:
    runs = _daily(db_session, world, [100, 104, 96, 102, 98, 100, 101, 400], last=NOW)
    slow = pb.score_duration(db_session, runs[-1])
    normal = pb.score_duration(db_session, runs[-2])
    assert slow is not None and slow.z_score is not None and slow.z_score > 10
    assert normal is not None and normal.z_score is not None and normal.z_score < 1


def test_a_clockwork_pipeline_is_not_flagged_for_a_tiny_wobble(
    db_session: Any, world: dict[str, Any]
) -> None:
    """Zero variance would make any change infinitely unusual; the floor is 10% of the mean."""
    runs = _daily(db_session, world, [60, 60, 60, 60, 60, 60, 61], last=NOW)
    score = pb.score_duration(db_session, runs[-1])
    assert score is not None and score.z_score is not None and score.z_score < 1


def test_a_clockwork_pipeline_is_still_flagged_for_a_real_jump(
    db_session: Any, world: dict[str, Any]
) -> None:
    runs = _daily(db_session, world, [60, 60, 60, 60, 60, 60, 600], last=NOW)
    score = pb.score_duration(db_session, runs[-1])
    assert score is not None and score.z_score is not None and score.z_score > 50


def test_other_envs_and_later_runs_are_not_history(db_session: Any, world: dict[str, Any]) -> None:
    runs = _daily(db_session, world, [100, 100, 100, 100, 100, 100], last=NOW)
    for i in range(6):
        _run(db_session, world, started=NOW - timedelta(days=i, hours=1), seconds=9000, env="prod")
    _run(db_session, world, started=NOW + timedelta(days=1), seconds=9000)
    score = pb.score_duration(db_session, runs[-1])
    assert score is not None and score.points == 5 and score.mean_seconds == 100


def test_overdue_uses_the_cadence_threshold(db_session: Any, world: dict[str, Any]) -> None:
    _daily(db_session, world, [100] * 6, last=NOW - timedelta(days=3))
    overdue = pb.evaluate_overdue(
        db_session, provider="airflow", pipeline_or_dag_id="load_orders", env="dev", now=NOW
    )
    # Daily runs: largest gap 24 h, threshold 30 h; the last run was 72 h ago.
    assert overdue is not None and overdue.threshold_hours == 30.0
    assert overdue.hours_since_last_success == pytest.approx(72.0, abs=0.01)
    assert (
        pb.evaluate_overdue(
            db_session,
            provider="airflow",
            pipeline_or_dag_id="load_orders",
            env="dev",
            now=NOW - timedelta(days=2),
        )
        is None
    )


def _flag(db_session: Any) -> WorkspaceHealth | None:
    flag: WorkspaceHealth | None = db_session.scalar(
        select(WorkspaceHealth).where(
            WorkspaceHealth.key == pb.signal_key("airflow", "load_orders", "dev")
        )
    )
    return flag


def test_a_slow_run_alerts_once_then_recovers(
    db_session: Any, world: dict[str, Any], publisher: _Publisher
) -> None:
    _daily(db_session, world, [100, 104, 96, 102, 98, 100, 101, 400], last=NOW - timedelta(hours=1))

    assert pb.run_pipeline_baseline_check(db_session, now=NOW)["alerted"] == 1
    assert pb.run_pipeline_baseline_check(db_session, now=NOW)["ok"] == 1  # no re-alert
    [report] = publisher.reports
    assert report.is_failing and report.duration_seconds == 400 and not report.is_overdue
    flag = _flag(db_session)
    assert flag is not None and flag.alerted_at == NOW
    assert flag.payload is not None and flag.payload["reason"] == "duration"

    _run(db_session, world, started=NOW + timedelta(minutes=5), seconds=100)
    later = NOW + timedelta(hours=1)
    assert pb.run_pipeline_baseline_check(db_session, now=later)["recovered"] == 1
    assert not publisher.reports[-1].is_failing
    flag = _flag(db_session)
    assert flag is not None and flag.alerted_at is None


def test_an_overdue_pipeline_alerts(
    db_session: Any, world: dict[str, Any], publisher: _Publisher
) -> None:
    _daily(db_session, world, [100] * 6, last=NOW - timedelta(days=3))
    assert pb.run_pipeline_baseline_check(db_session, now=NOW)["alerted"] == 1
    assert publisher.reports[0].is_overdue


def test_undeliverable_is_retried_not_recorded(
    db_session: Any, world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pb, "get_health_publisher", lambda: _Publisher(undeliverable=True))
    _daily(db_session, world, [100] * 6, last=NOW - timedelta(days=3))
    assert pb.run_pipeline_baseline_check(db_session, now=NOW)["undeliverable"] == 1
    flag = _flag(db_session)
    assert flag is None or flag.alerted_at is None


def test_an_unbound_pipeline_is_not_watched(
    db_session: Any, world: dict[str, Any], publisher: _Publisher
) -> None:
    for binding in db_session.scalars(select(TriggerBinding)):
        binding.enabled = False
    db_session.commit()
    _daily(db_session, world, [100] * 6, last=NOW - timedelta(days=3))
    assert pb.run_pipeline_baseline_check(db_session, now=NOW) == {
        "alerted": 0,
        "recovered": 0,
        "undeliverable": 0,
        "ok": 0,
    }


def test_zero_threshold_disables_the_check(
    db_session: Any, world: dict[str, Any], publisher: _Publisher, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "pipeline_baseline_z_threshold", 0.0)
    _daily(db_session, world, [100] * 6, last=NOW - timedelta(days=3))
    assert pb.run_pipeline_baseline_check(db_session, now=NOW)["alerted"] == 0
    assert publisher.reports == []
