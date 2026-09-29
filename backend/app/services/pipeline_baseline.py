"""Baselined pipeline-run signals (#1653): an unusual run duration, and an overdue pipeline.

These monitor ORCHESTRATION rows, not datasets, so they are deliberately not a `check.kind`
(ADR 0012; ADF/Airflow/dbt are not datasources). `pipeline_runs` is already the history, so
nothing extra is stored: a run is scored against the prior successful runs of the same
(provider, pipeline, env). The beat task `check_pipeline_baselines` evaluates every bound
pipeline and delivers edges through the workspace-signal path (`workspace_health`, delivered
first); the incident evidence card reads the same duration score.
"""

from __future__ import annotations

import functools
import hashlib
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.app.alerting.base import (
    HEALTH_FAILING,
    HEALTH_RECOVERED,
    AlertUndeliverableError,
    PipelineBaselineReport,
)
from backend.app.alerting.registry import get_health_publisher
from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger
from backend.app.db.models import Connection, PipelineRun, TriggerBinding, WorkspaceHealth
from backend.app.services.orchestration_service import compute_pipeline_cadence

#: Prior successful runs a duration is scored against, and the fewest that make a baseline.
WINDOW = 20
MIN_POINTS = 5
#: A standard deviation below this share of the mean is floored to it, so a pipeline that always
#: takes exactly 60 s is not flagged for taking 61 s.
STDDEV_FLOOR_RATIO = 0.1
SIGNAL_KEY_PREFIX = "pipeline_baseline:"

log = get_logger(__name__)


@dataclass(frozen=True)
class DurationScore:
    """A run's duration against the prior successful runs of the same pipeline and env."""

    duration_seconds: float
    points: int
    mean_seconds: float | None = None
    stddev_seconds: float | None = None
    z_score: float | None = None

    @property
    def insufficient_history(self) -> bool:
        return self.points < MIN_POINTS

    @property
    def delay_seconds(self) -> float | None:
        """Positive = slower than the prior runs' mean."""
        return None if self.mean_seconds is None else self.duration_seconds - self.mean_seconds


def run_duration_seconds(run: PipelineRun) -> float | None:
    if run.started_at is None or run.finished_at is None:
        return None
    return float((run.finished_at - run.started_at).total_seconds())


def score_duration(session: Session, run: PipelineRun) -> DurationScore | None:
    """``None`` when the run has no duration. With fewer than ``MIN_POINTS`` priors the mean is
    still reported (for the evidence card's delay) but there is no z-score."""
    value = run_duration_seconds(run)
    if value is None or run.started_at is None:
        return None
    rows = session.execute(
        select(PipelineRun.started_at, PipelineRun.finished_at)
        .where(
            PipelineRun.provider == run.provider,
            PipelineRun.pipeline_or_dag_id == run.pipeline_or_dag_id,
            PipelineRun.env == run.env,
            PipelineRun.id != run.id,
            PipelineRun.status == "succeeded",
            PipelineRun.started_at.is_not(None),
            PipelineRun.finished_at.is_not(None),
            PipelineRun.started_at < run.started_at,
        )
        .order_by(PipelineRun.started_at.desc())
        .limit(WINDOW)
    ).all()
    priors = [float((fin - start).total_seconds()) for start, fin in rows]
    if not priors:
        return DurationScore(duration_seconds=value, points=0)
    mean = statistics.fmean(priors)
    if len(priors) < MIN_POINTS:
        return DurationScore(duration_seconds=value, points=len(priors), mean_seconds=mean)
    stddev = statistics.stdev(priors)
    floored = max(stddev, STDDEV_FLOOR_RATIO * abs(mean))
    z = abs(value - mean) / floored if floored > 0 else 0.0
    return DurationScore(
        duration_seconds=value,
        points=len(priors),
        mean_seconds=mean,
        stddev_seconds=stddev,
        z_score=z,
    )


@dataclass(frozen=True)
class Overdue:
    hours_since_last_success: float
    threshold_hours: float


def evaluate_overdue(
    session: Session, *, provider: str, pipeline_or_dag_id: str, env: str, now: datetime
) -> Overdue | None:
    """Overdue when the time since the last successful run exceeds the pipeline's cadence
    threshold — the same definition the freshness-threshold hint uses (#1648). ``None`` when
    not overdue or there is too little history to say."""
    cadence = compute_pipeline_cadence(
        session, provider=provider, pipeline_or_dag_id=pipeline_or_dag_id, env=env
    )
    if cadence.insufficient_history or cadence.suggested_fail_threshold_hours is None:
        return None
    last = session.scalar(
        select(func.max(func.coalesce(PipelineRun.started_at, PipelineRun.created_at))).where(
            PipelineRun.provider == provider,
            PipelineRun.pipeline_or_dag_id == pipeline_or_dag_id,
            PipelineRun.env == env,
            PipelineRun.status == "succeeded",
        )
    )
    if last is None:
        return None
    hours = (now - last).total_seconds() / 3600
    if hours <= cadence.suggested_fail_threshold_hours:
        return None
    return Overdue(
        hours_since_last_success=hours, threshold_hours=cadence.suggested_fail_threshold_hours
    )


#: A connection not polled within this long is not a reliable source of "no new runs".
_INGEST_FRESH_S = 1800


def ingestion_healthy(
    session: Session, *, provider: str, pipeline_or_dag_id: str, env: str, now: datetime
) -> bool:
    """Whether the connection that records this pipeline's runs is being polled successfully.

    If it isn't, a missing run proves nothing: the pipeline may be running fine upstream while
    DataQ sees no new rows, and the poll-health alerts already name that cause.
    """
    connection_id = session.scalar(
        select(PipelineRun.connection_id)
        .where(
            PipelineRun.provider == provider,
            PipelineRun.pipeline_or_dag_id == pipeline_or_dag_id,
            PipelineRun.env == env,
        )
        .order_by(PipelineRun.created_at.desc())
        .limit(1)
    )
    connection = session.get(Connection, connection_id) if connection_id else None
    if connection is None or connection.last_polled_at is None:
        return False
    return (connection.consecutive_poll_failures or 0) == 0 and (
        now - connection.last_polled_at
    ).total_seconds() <= _INGEST_FRESH_S


def bound_pipelines(session: Session) -> list[tuple[str, str, str]]:
    """Every (provider, pipeline, env) at least one enabled binding points a suite at."""
    rows = session.execute(
        select(TriggerBinding.provider, TriggerBinding.pipeline_or_dag_id, TriggerBinding.env)
        .where(TriggerBinding.enabled.is_(True))
        .distinct()
        .order_by(TriggerBinding.provider, TriggerBinding.pipeline_or_dag_id, TriggerBinding.env)
    ).all()
    return [(p, pid, env) for p, pid, env in rows]


def signal_key(provider: str, pipeline_or_dag_id: str, env: str) -> str:
    """A `workspace_health` key (≤ 64 chars) for one pipeline; the identity lives in the payload."""
    digest = hashlib.sha256(f"{provider}\x00{pipeline_or_dag_id}\x00{env}".encode()).hexdigest()
    return SIGNAL_KEY_PREFIX + digest[:40]


def evaluate_pipeline(
    session: Session,
    *,
    provider: str,
    pipeline_or_dag_id: str,
    env: str,
    z_threshold: float,
    now: datetime | None = None,
) -> PipelineBaselineReport | None:
    """The pipeline's current state: failing (overdue, or its latest successful run's duration
    is ``z_threshold`` or more standard deviations out), recovered, or ``None`` = unknown: the
    runs DataQ has are fine, but its polling of this pipeline is not, so it may be overdue."""
    moment = now or datetime.now(UTC)
    report = functools.partial(
        PipelineBaselineReport, provider=provider, pipeline_or_dag_id=pipeline_or_dag_id, env=env
    )
    ingesting = ingestion_healthy(
        session, provider=provider, pipeline_or_dag_id=pipeline_or_dag_id, env=env, now=moment
    )
    overdue = (
        evaluate_overdue(
            session, provider=provider, pipeline_or_dag_id=pipeline_or_dag_id, env=env, now=moment
        )
        if ingesting
        else None
    )
    if overdue is not None:
        return report(
            state=HEALTH_FAILING,
            hours_since_last_success=overdue.hours_since_last_success,
            overdue_threshold_hours=overdue.threshold_hours,
        )
    latest = session.scalars(
        select(PipelineRun)
        .where(
            PipelineRun.provider == provider,
            PipelineRun.pipeline_or_dag_id == pipeline_or_dag_id,
            PipelineRun.env == env,
            PipelineRun.status == "succeeded",
            PipelineRun.started_at.is_not(None),
            PipelineRun.finished_at.is_not(None),
        )
        .order_by(PipelineRun.started_at.desc())
        .limit(1)
    ).first()
    score = score_duration(session, latest) if latest is not None else None
    if score is not None and score.z_score is not None and score.z_score >= z_threshold:
        return report(
            state=HEALTH_FAILING,
            duration_seconds=score.duration_seconds,
            mean_duration_seconds=score.mean_seconds,
            z_score=score.z_score,
        )
    return report(state=HEALTH_RECOVERED) if ingesting else None


def _payload(report: PipelineBaselineReport, alerted: bool) -> dict[str, Any]:
    return {
        "provider": report.provider,
        "pipeline_or_dag_id": report.pipeline_or_dag_id,
        "env": report.env,
        "reason": (
            "overdue" if report.is_overdue else "duration" if report.is_slow_or_fast else None
        ),
        "alerted": alerted,
    }


def _clear_unwatched(session: Session, watched_keys: set[str]) -> int:
    """Close outstanding alerts for pipelines no longer watched (binding disabled or removed, or
    the check turned off). No recovery message: nothing says the pipeline is back to normal."""
    query = select(WorkspaceHealth).where(
        WorkspaceHealth.key.startswith(SIGNAL_KEY_PREFIX),
        WorkspaceHealth.alerted_at.is_not(None),
    )
    if watched_keys:
        query = query.where(WorkspaceHealth.key.not_in(watched_keys))
    stale = session.scalars(query.with_for_update(skip_locked=True)).all()
    for flag in stale:
        flag.alerted_at = None
        flag.payload = {**(flag.payload or {}), "alerted": False, "reason": "unwatched"}
        log.info("pipeline_baseline_cleared_unwatched", key=flag.key)
    session.commit()
    return len(stale)


def run_pipeline_baseline_check(session: Session, *, now: datetime | None = None) -> dict[str, int]:
    """One tick over every bound pipeline; returns outcome counts for logs and tests.

    Same delivered-first rule as poll staleness (#843): a failure edge is recorded only once a
    channel actually sent it, so an undeliverable alert is retried every tick, and the recovery
    edge is sent once when the pipeline is back within its baseline.
    """
    counts = {"alerted": 0, "recovered": 0, "undeliverable": 0, "ok": 0, "unknown": 0}
    threshold = get_settings().pipeline_baseline_z_threshold
    watched = [] if threshold <= 0 else bound_pipelines(session)
    counts["cleared"] = _clear_unwatched(
        session, {signal_key(p, pid, env) for p, pid, env in watched}
    )
    moment = now or datetime.now(UTC)
    for provider, pipeline_or_dag_id, env in watched:
        key = signal_key(provider, pipeline_or_dag_id, env)
        session.execute(
            pg_insert(WorkspaceHealth)
            .values(key=key)
            .on_conflict_do_nothing(index_elements=[WorkspaceHealth.key])
        )
        flag = session.execute(
            select(WorkspaceHealth)
            .where(WorkspaceHealth.key == key)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if flag is None:  # another tick holds it
            session.rollback()
            continue
        report = evaluate_pipeline(
            session,
            provider=provider,
            pipeline_or_dag_id=pipeline_or_dag_id,
            env=env,
            z_threshold=threshold,
            now=moment,
        )
        if report is None:
            session.rollback()
            counts["unknown"] += 1
            continue
        outstanding = flag.alerted_at is not None
        if report.is_failing == outstanding:
            session.rollback()
            counts["ok"] += 1
            continue
        try:
            get_health_publisher().publish_workspace_signal(session, report)
        except AlertUndeliverableError:
            session.rollback()
            log.warning(
                "pipeline_baseline_undeliverable",
                provider=provider,
                pipeline_or_dag_id=pipeline_or_dag_id,
                env=env,
                failing=report.is_failing,
            )
            counts["undeliverable"] += 1
            continue
        flag.alerted_at = moment if report.is_failing else None
        flag.payload = _payload(report, alerted=report.is_failing)
        session.commit()
        log.info(
            "pipeline_baseline_alerted" if report.is_failing else "pipeline_baseline_recovered",
            provider=provider,
            pipeline_or_dag_id=pipeline_or_dag_id,
            env=env,
            reason=flag.payload["reason"],
            z_score=report.z_score,
        )
        counts["alerted" if report.is_failing else "recovered"] += 1
    return counts
