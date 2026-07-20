"""Dashboard aggregates — the read model behind the Enhanced Monitoring
Dashboard (Week 6, ADR 0022).

Every aggregate is **suite-scoped** through the same owned-or-shared filter the
runs read model uses (`suite_service.accessible_suite_ids`), so the dashboard can
never surface a run/result from a suite the caller can't see — the same reason
reading Postgres directly (a Grafana panel) is rejected as the product surface
(ADR 0018). Aggregation is done in SQL over the persisted `status` column
(ADR 0005 health score) — never by reducing JSONB in Python (ADR 0012).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.db.models import Result, Run, Suite
from backend.app.services import suite_service
from backend.app.services.rollup import (
    health_score,
    latest_runs_per_suite_stmt,
    pass_rate,
    performance_state,
)

# The ADR-0005 score math now lives in `services/rollup.py`, shared with the asset
# view and the #889 scorecard (one helper, not one-per-consumer). Re-exported here
# because the dashboard API/MCP layers and its tests import them from this module,
# and because "the dashboard's health score" is still a meaningful name for them.
__all__ = [
    "dashboard_summary",
    "health_score",
    "pass_rate",
    "performance_state",
]


# ── summary shape ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Kpis:
    health_score: float | None
    pass_rate: float | None
    total_runs: int
    active_connections: int
    # ── #352 enrichments ──
    # Mean run duration over the window (finished runs only); None when no run
    # in the window finished.
    avg_duration_ms: float | None = None
    # Period-over-period deltas vs the previous equivalent window. Score/rate
    # deltas are in POINTS (both are already percentages); runs/duration deltas
    # are % change. None whenever either side has no data — an honest blank,
    # never a fabricated 0 (KPI honesty, ADR 0022/0018).
    health_score_delta: float | None = None
    pass_rate_delta: float | None = None
    total_runs_delta_pct: float | None = None
    avg_duration_delta_pct: float | None = None


@dataclass(frozen=True)
class TrendPoint:
    day: date
    succeeded: int
    failed: int


@dataclass(frozen=True)
class SuitePerformance:
    suite_id: uuid.UUID
    name: str
    score: float | None
    state: str


@dataclass(frozen=True)
class DashboardSummary:
    window_days: int
    kpis: Kpis
    trend: list[TrendPoint]
    suite_performance: list[SuitePerformance]


def _window_start(window_days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=window_days)


def _status_counts(
    session: Session,
    accessible: Select[tuple[uuid.UUID]],
    since: datetime,
    until: datetime | None = None,
) -> dict[str, int]:
    """Histogram of result statuses across accessible suites in ``[since, until)``
    (open-ended when ``until`` is None)."""
    stmt = (
        select(Result.status, func.count())
        .join(Run, Result.run_id == Run.id)
        .where(Run.suite_id.in_(accessible), Result.created_at >= since)
        .group_by(Result.status)
    )
    if until is not None:
        stmt = stmt.where(Result.created_at < until)
    counts: dict[str, int] = {}
    for status, count in session.execute(stmt):
        counts[status] = count
    return counts


def _run_trend(
    session: Session, accessible: Select[tuple[uuid.UUID]], since: datetime
) -> list[TrendPoint]:
    """Per-day succeeded/failed run counts, zero-filled across the window so the
    chart has a contiguous x-axis even on quiet days.

    Days are bucketed in UTC (``timezone('UTC', …)``) so SQL bucketing agrees
    with the UTC zero-fill cursor below regardless of the DB session timezone —
    otherwise a run near midnight could bucket into a day the cursor never emits.
    """
    day = func.date(func.timezone("UTC", Run.created_at))
    stmt = (
        select(day, Run.status, func.count())
        .where(Run.suite_id.in_(accessible), Run.created_at >= since)
        .group_by(day, Run.status)
    )
    by_day: dict[date, dict[str, int]] = {}
    for d, status, count in session.execute(stmt):
        # func.date returns a date on psycopg2; normalise just in case a driver
        # hands back a datetime.
        key = d.date() if isinstance(d, datetime) else d
        by_day.setdefault(key, {})[status] = count

    start = since.date()
    today = datetime.now(UTC).date()
    out: list[TrendPoint] = []
    cursor = start
    while cursor <= today:
        counts = by_day.get(cursor, {})
        out.append(
            TrendPoint(
                day=cursor,
                succeeded=counts.get("succeeded", 0),
                failed=counts.get("failed", 0),
            )
        )
        cursor += timedelta(days=1)
    return out


def _suite_performance(
    session: Session, accessible: Select[tuple[uuid.UUID]]
) -> list[SuitePerformance]:
    """Per-suite health from each suite's **latest** run, worst (lowest) first.

    A suite with no run, or whose latest run wrote no results (a hard-failed run
    rolls back its results), is omitted — there is no health to show.
    """
    # The shared latest-run-per-suite statement (#889) — kept in SQL here and
    # inner-joined, which is what drops a suite whose latest run wrote no results.
    latest = latest_runs_per_suite_stmt(accessible).subquery()
    stmt = (
        select(Suite.id, Suite.name, Result.status, func.count())
        .select_from(latest)
        .join(Suite, Suite.id == latest.c.suite_id)
        .join(Result, Result.run_id == latest.c.id)
        .group_by(Suite.id, Suite.name, Result.status)
    )
    counts: dict[uuid.UUID, dict[str, int]] = {}
    names: dict[uuid.UUID, str] = {}
    for sid, name, status, count in session.execute(stmt):
        counts.setdefault(sid, {})[status] = count
        names[sid] = name

    out = [
        SuitePerformance(
            suite_id=sid,
            name=names[sid],
            score=health_score(c),
            state=performance_state(health_score(c)),
        )
        for sid, c in counts.items()
    ]
    # Worst first (lowest score), suites with no severity result (score None) last.
    out.sort(key=lambda s: (s.score is None, s.score if s.score is not None else 0.0))
    return out


def _active_connections(session: Session, accessible: Select[tuple[uuid.UUID]]) -> int:
    """Distinct connections referenced by the caller's accessible suites."""
    stmt = select(func.count(func.distinct(Suite.connection_id))).where(Suite.id.in_(accessible))
    return session.scalar(stmt) or 0


def _total_runs(
    session: Session,
    accessible: Select[tuple[uuid.UUID]],
    since: datetime,
    until: datetime | None = None,
) -> int:
    stmt = (
        select(func.count())
        .select_from(Run)
        .where(Run.suite_id.in_(accessible), Run.created_at >= since)
    )
    if until is not None:
        stmt = stmt.where(Run.created_at < until)
    return session.scalar(stmt) or 0


def _avg_duration_ms(
    session: Session,
    accessible: Select[tuple[uuid.UUID]],
    since: datetime,
    until: datetime | None = None,
) -> float | None:
    """Mean run duration (finished - started, ms) over runs created in the
    window. Runs still in flight / never started are excluded; ``None`` when
    nothing in the window finished (an honest blank, not 0)."""
    duration_s = func.extract("epoch", Run.finished_at - Run.started_at)
    stmt = (
        select(func.avg(duration_s))
        .select_from(Run)
        .where(
            Run.suite_id.in_(accessible),
            Run.created_at >= since,
            Run.started_at.is_not(None),
            Run.finished_at.is_not(None),
            # Clock skew / backfill can leave finished < started; a negative
            # interval would poison the mean (and the card renders it as junk).
            Run.finished_at >= Run.started_at,
        )
    )
    if until is not None:
        stmt = stmt.where(Run.created_at < until)
    avg_s = session.scalar(stmt)
    return None if avg_s is None else round(float(avg_s) * 1000.0, 1)


def _delta_points(current: float | None, previous: float | None) -> float | None:
    """current - previous, for metrics that are already percentages."""
    if current is None or previous is None:
        return None
    return round(current - previous, 1)


def _delta_pct(current: float | None, previous: float | None) -> float | None:
    """% change vs the previous window; ``None`` when previous is missing/zero
    (a delta against nothing is meaningless, not +∞)."""
    if current is None or previous is None or previous == 0:
        return None
    return round((current - previous) / previous * 100.0, 1)


def dashboard_summary(
    session: Session, *, user_id: uuid.UUID, window_days: int, include_all: bool = False
) -> DashboardSummary:
    """KPIs + run trend + per-suite performance for the caller's accessible suites
    over the trailing ``window_days`` — or every suite when ``include_all`` (the
    workspace-admin view, ADR 0027)."""
    accessible = suite_service.accessible_suite_ids(user_id, include_all=include_all)
    since = _window_start(window_days)
    # Previous equivalent window, for period-over-period deltas (#352):
    # [now-2w, now-w) against the current [now-w, now].
    prev_since = since - timedelta(days=window_days)

    counts = _status_counts(session, accessible, since)
    prev_counts = _status_counts(session, accessible, prev_since, until=since)
    score = health_score(counts)
    rate = pass_rate(counts)
    total_runs = _total_runs(session, accessible, since)
    prev_total_runs = _total_runs(session, accessible, prev_since, until=since)
    avg_duration = _avg_duration_ms(session, accessible, since)
    prev_avg_duration = _avg_duration_ms(session, accessible, prev_since, until=since)
    kpis = Kpis(
        health_score=score,
        pass_rate=rate,
        total_runs=total_runs,
        active_connections=_active_connections(session, accessible),
        avg_duration_ms=avg_duration,
        health_score_delta=_delta_points(score, health_score(prev_counts)),
        pass_rate_delta=_delta_points(rate, pass_rate(prev_counts)),
        total_runs_delta_pct=_delta_pct(float(total_runs), float(prev_total_runs)),
        avg_duration_delta_pct=_delta_pct(avg_duration, prev_avg_duration),
    )
    return DashboardSummary(
        window_days=window_days,
        kpis=kpis,
        trend=_run_trend(session, accessible, since),
        suite_performance=_suite_performance(session, accessible),
    )
