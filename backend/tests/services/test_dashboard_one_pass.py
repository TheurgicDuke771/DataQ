"""The one-pass window aggregates (#1997) — same answer, fewer statements.

The reference implementation below is the per-window shape `dashboard_summary`
used before the collapse: one statement per aggregate per window. It is the
oracle for the equivalence test, so "byte-identical" is asserted against code
rather than against a number someone typed in.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Select, event, func, select

from backend.app.db.models import Check, Connection, Result, Run, Share, Suite, User
from backend.app.services import dashboard_service as svc
from backend.app.services import scoring_settings_service, suite_service
from backend.app.services.rollup import AGGREGATABLE_RUN_STATUSES

# ── the pre-#1997 per-window implementation, kept as the oracle ──────────────


def _ref_status_counts(
    session: Any,
    accessible: Select[tuple[uuid.UUID]],
    since: datetime,
    until: datetime | None = None,
) -> dict[str, int]:
    stmt = (
        select(Result.status, func.count())
        .join(Run, Result.run_id == Run.id)
        .where(
            Run.suite_id.in_(accessible),
            Run.status.in_(AGGREGATABLE_RUN_STATUSES),
            Result.created_at >= since,
        )
        .group_by(Result.status)
    )
    if until is not None:
        stmt = stmt.where(Result.created_at < until)
    return dict(session.execute(stmt).all())


def _ref_total_runs(
    session: Any,
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


def _ref_avg_duration_ms(
    session: Any,
    accessible: Select[tuple[uuid.UUID]],
    since: datetime,
    until: datetime | None = None,
) -> float | None:
    duration_s = func.extract("epoch", Run.finished_at - Run.started_at)
    stmt = (
        select(func.avg(duration_s))
        .select_from(Run)
        .where(
            Run.suite_id.in_(accessible),
            Run.created_at >= since,
            Run.started_at.is_not(None),
            Run.finished_at.is_not(None),
            Run.finished_at >= Run.started_at,
        )
    )
    if until is not None:
        stmt = stmt.where(Run.created_at < until)
    avg_s = session.scalar(stmt)
    return None if avg_s is None else round(float(avg_s) * 1000.0, 1)


def _reference_kpis(
    session: Any, *, user_id: uuid.UUID, window_days: int, since: datetime
) -> svc.Kpis:
    """The KPI block exactly as the ten-statement implementation computed it."""
    accessible = suite_service.accessible_suite_ids(user_id)
    prev_since = since - timedelta(days=window_days)
    weights = scoring_settings_service.weights(session)
    counts = _ref_status_counts(session, accessible, since)
    prev_counts = _ref_status_counts(session, accessible, prev_since, until=since)
    score = svc.health_score(counts, weights)
    rate = svc.pass_rate(counts)
    total_runs = _ref_total_runs(session, accessible, since)
    prev_total_runs = _ref_total_runs(session, accessible, prev_since, until=since)
    avg_duration = _ref_avg_duration_ms(session, accessible, since)
    prev_avg_duration = _ref_avg_duration_ms(session, accessible, prev_since, until=since)
    return svc.Kpis(
        health_score=score,
        pass_rate=rate,
        total_runs=total_runs,
        active_connections=svc._active_connections(session, accessible),
        avg_duration_ms=avg_duration,
        health_score_delta=svc._delta_points(score, svc.health_score(prev_counts, weights)),
        pass_rate_delta=svc._delta_points(rate, svc.pass_rate(prev_counts)),
        total_runs_delta_pct=svc._delta_pct(float(total_runs), float(prev_total_runs)),
        avg_duration_delta_pct=svc._delta_pct(avg_duration, prev_avg_duration),
    )


# ── seeding ──────────────────────────────────────────────────────────────────


def _user(db: Any) -> User:
    u = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:6]}@example.com")
    db.add(u)
    db.flush()
    return u


def _suite(db: Any, owner: User, *, name: str) -> Suite:
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "a"},
        secret_ref="kv",
        created_by=owner.id,
    )
    db.add(conn)
    db.flush()
    suite = Suite(name=name, connection_id=conn.id, created_by=owner.id, target={"table": "T"})
    db.add(suite)
    db.flush()
    return suite


def _run(
    db: Any,
    suite: Suite,
    *,
    run_status: str,
    result_statuses: list[str],
    age_days: float,
    duration_s: float | None = None,
) -> None:
    when = datetime.now(UTC) - timedelta(days=age_days)
    run = Run(suite_id=suite.id, status=run_status, created_at=when)
    if duration_s is not None:
        run.started_at = when
        run.finished_at = when + timedelta(seconds=duration_s)
    db.add(run)
    db.flush()
    for s in result_statuses:
        check = Check(
            suite_id=suite.id, name=f"chk-{uuid.uuid4().hex[:6]}", expectation_type="e", config={}
        )
        db.add(check)
        db.flush()
        db.add(Result(run_id=run.id, check_id=check.id, status=s, created_at=when))


def _seed_both_windows(db: Any) -> User:
    """A population that exercises every branch the collapse could break: both
    windows, every severity tier, skip/error, a shared suite, an unshared one,
    an in-flight run, a clock-skewed run, and rows outside both windows.
    """
    alice, bob = _user(db), _user(db)
    mine = _suite(db, alice, name="mine")
    shared = _suite(db, bob, name="shared")
    db.add(Share(suite_id=shared.id, user_id=alice.id, permission="view"))
    hidden = _suite(db, bob, name="hidden")

    # Current window.
    _run(
        db,
        mine,
        run_status="succeeded",
        result_statuses=["pass", "warn"],
        age_days=0.5,
        duration_s=2.0,
    )
    _run(
        db,
        mine,
        run_status="succeeded",
        result_statuses=["fail", "skip"],
        age_days=3,
        duration_s=6.0,
    )
    # Deliberately NOT age_days=1: that is exactly the `window_days=1` boundary, and a
    # row sitting on it classifies by which of the two `_window_start` calls ran first.
    _run(
        db,
        shared,
        run_status="succeeded",
        result_statuses=["critical"],
        age_days=1.2,
        duration_s=4.0,
    )
    _run(db, mine, run_status="failed", result_statuses=[], age_days=2)
    _run(db, mine, run_status="running", result_statuses=[], age_days=0.1)
    _run(db, mine, run_status="succeeded", result_statuses=["pass"], age_days=1.5, duration_s=-9.0)
    # A status that exists ONLY in the previous window — the collapse must not
    # carry it into the current one as a zero.
    _run(
        db,
        mine,
        run_status="succeeded",
        result_statuses=["error", "pass"],
        age_days=9,
        duration_s=8.0,
    )
    _run(
        db,
        shared,
        run_status="succeeded",
        result_statuses=["critical", "fail"],
        age_days=12,
        duration_s=10.0,
    )
    _run(db, mine, run_status="failed", result_statuses=[], age_days=10)
    # Outside both windows entirely.
    _run(db, mine, run_status="succeeded", result_statuses=["pass"], age_days=40, duration_s=99.0)
    # Never visible to alice.
    _run(
        db, hidden, run_status="succeeded", result_statuses=["critical"], age_days=1, duration_s=1.0
    )
    db.commit()
    return alice


# ── equivalence ──────────────────────────────────────────────────────────────


def test_one_pass_kpis_match_the_per_window_reference(db_session: Any) -> None:
    alice = _seed_both_windows(db_session)

    summary = svc.dashboard_summary(db_session, user_id=alice.id, window_days=7)
    reference = _reference_kpis(
        db_session, user_id=alice.id, window_days=7, since=svc._window_start(7)
    )

    assert summary.kpis == reference
    # Not a vacuous pass: every field the collapse touches has a real value here.
    assert summary.kpis.health_score is not None
    assert summary.kpis.total_runs > 0
    assert summary.kpis.avg_duration_ms is not None
    assert summary.kpis.health_score_delta is not None
    assert summary.kpis.pass_rate_delta is not None
    assert summary.kpis.total_runs_delta_pct is not None
    assert summary.kpis.avg_duration_delta_pct is not None


def test_previous_window_status_does_not_leak_as_a_current_zero(db_session: Any) -> None:
    """`error` exists only 9 days back. A FILTER aggregate reports 0 for it in the
    current window; the dict must simply not carry the key, as it did not before.
    """
    alice = _seed_both_windows(db_session)
    since = svc._window_start(7)
    visible = suite_service.accessible_suite_filter(Run.suite_id, alice.id)

    counts, prev_counts = svc._status_counts(db_session, visible, since, since - timedelta(days=7))

    assert "error" in prev_counts
    assert "error" not in counts
    assert 0 not in counts.values()
    assert 0 not in prev_counts.values()


def test_admin_include_all_matches_the_per_suite_scope(db_session: Any) -> None:
    """`include_all` is the whole workspace — the array-form visibility predicate
    must be the same population the subquery form selected."""
    alice = _seed_both_windows(db_session)
    assert len(db_session.execute(select(Suite.id)).scalars().all()) == 3

    as_admin = svc.dashboard_summary(db_session, user_id=alice.id, window_days=7, include_all=True)
    as_alice = svc.dashboard_summary(db_session, user_id=alice.id, window_days=7)

    # `hidden` adds a critical result and a run, so admin sees strictly more.
    assert as_admin.kpis.total_runs > as_alice.kpis.total_runs
    assert as_admin.kpis.health_score is not None
    assert as_alice.kpis.health_score is not None
    assert as_admin.kpis.health_score < as_alice.kpis.health_score
    admin_names = {s.name for s in as_admin.suite_performance}
    assert admin_names - {s.name for s in as_alice.suite_performance} == {"hidden"}


# ── statement count ──────────────────────────────────────────────────────────

#: Weights, result-status histogram (both windows), run count + duration (both
#: windows), active connections, trend, per-suite performance.
EXPECTED_STATEMENTS = 6


def test_summary_issues_one_pass_per_aggregate(db_session: Any) -> None:
    alice_id = _seed_both_windows(db_session).id

    statements: list[str] = []

    def _record(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(db_session.bind, "before_cursor_execute", _record)
    try:
        svc.dashboard_summary(db_session, user_id=alice_id, window_days=7)
    finally:
        event.remove(db_session.bind, "before_cursor_execute", _record)

    assert len(statements) == EXPECTED_STATEMENTS, "\n".join(statements)
    # The two windows share one scan each, so neither aggregate is issued twice.
    assert sum("FROM results JOIN runs" in s for s in statements) == 1
    assert sum("avg(EXTRACT(epoch" in s for s in statements) == 1


@pytest.mark.parametrize("window_days", [1, 7, 30, 90])
def test_equivalence_holds_across_window_sizes(db_session: Any, window_days: int) -> None:
    alice = _seed_both_windows(db_session)
    summary = svc.dashboard_summary(db_session, user_id=alice.id, window_days=window_days)
    reference = _reference_kpis(
        db_session,
        user_id=alice.id,
        window_days=window_days,
        since=svc._window_start(window_days),
    )
    assert summary.kpis == reference
