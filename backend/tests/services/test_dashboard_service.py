"""Tests for the dashboard aggregates service.

Two layers: the pure ADR-0005 health-score math (no DB), and the DB-backed
summary aggregation — scoping (owned-or-shared only), the window cutoff, the
run trend zero-fill, and per-suite performance from the latest run. Skips
without TEST_DATABASE_URL.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any

import pytest

from backend.app.db.models import Check, Connection, Result, Run, Share, Suite, User
from backend.app.services import dashboard_service as svc

# ── pure health-score / pass-rate / state (ADR 0005) ─────────────────────────


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ({"pass": 4}, 100.0),
        ({"fail": 4}, 50.0),
        ({"warn": 4}, 75.0),
        ({"critical": 4}, 0.0),
        ({"fail": 2, "pass": 2}, 75.0),  # the ADR's worked example
        ({"pass": 1, "warn": 1, "fail": 1, "critical": 1}, 56.2),  # (0+.5+1+2)/(4*2)=.4375
        ({}, None),
        ({"skip": 3, "error": 2}, None),  # non-severity statuses excluded → N=0
    ],
)
def test_health_score(counts: dict[str, int], expected: float | None) -> None:
    assert svc.health_score(counts) == expected


def test_health_score_excludes_skip_and_error_from_n() -> None:
    # skip/error don't dilute the score — {pass, fail} alone is 75, with skip/error too.
    assert svc.health_score({"pass": 1, "fail": 1}) == 75.0
    assert svc.health_score({"pass": 1, "fail": 1, "skip": 10, "error": 5}) == 75.0


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ({"pass": 4}, 100.0),
        ({"pass": 1, "fail": 3}, 25.0),
        ({}, None),
        ({"skip": 5}, None),
    ],
)
def test_pass_rate(counts: dict[str, int], expected: float | None) -> None:
    assert svc.pass_rate(counts) == expected


@pytest.mark.parametrize(
    ("score", "state"),
    [
        (100.0, "optimal"),
        (90.0, "optimal"),
        (89.9, "stable"),
        (60.0, "stable"),
        (59.9, "critical"),
        (0.0, "critical"),
        (None, "unknown"),
    ],
)
def test_performance_state(score: float | None, state: str) -> None:
    assert svc.performance_state(score) == state


# ── DB-backed summary ────────────────────────────────────────────────────────


def _user(db: Any) -> User:
    u = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:6]}@example.com")
    db.add(u)
    db.flush()
    return u


def _suite(db: Any, owner: User, *, name: str = "s") -> Suite:
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


def _run_with_results(
    db: Any,
    suite: Suite,
    *,
    run_status: str,
    result_statuses: list[str],
    age_days: float = 0.0,
) -> Run:
    when = datetime.now(UTC) - timedelta(days=age_days)
    run = Run(suite_id=suite.id, status=run_status, created_at=when)
    db.add(run)
    db.flush()
    for s in result_statuses:
        check = Check(
            suite_id=suite.id, name=f"chk-{uuid.uuid4().hex[:6]}", expectation_type="e", config={}
        )
        db.add(check)
        db.flush()
        db.add(Result(run_id=run.id, check_id=check.id, status=s, created_at=when))
    db.commit()
    return run


def test_summary_scopes_to_accessible_suites(db_session: Any) -> None:
    alice, bob = _user(db_session), _user(db_session)
    mine = _suite(db_session, alice, name="mine")
    theirs = _suite(db_session, bob, name="theirs")
    _run_with_results(db_session, mine, run_status="succeeded", result_statuses=["pass", "pass"])
    _run_with_results(db_session, theirs, run_status="failed", result_statuses=["critical", "fail"])

    summary = svc.dashboard_summary(db_session, user_id=alice.id, window_days=7)

    # Bob's failing suite must not bleed into Alice's all-pass numbers.
    assert summary.kpis.health_score == 100.0
    assert summary.kpis.pass_rate == 100.0
    assert summary.kpis.active_connections == 1
    assert [s.name for s in summary.suite_performance] == ["mine"]


def test_summary_includes_shared_suite(db_session: Any) -> None:
    owner, viewer = _user(db_session), _user(db_session)
    suite = _suite(db_session, owner, name="shared")
    db_session.add(Share(suite_id=suite.id, user_id=viewer.id, permission="view"))
    db_session.commit()
    _run_with_results(db_session, suite, run_status="failed", result_statuses=["fail", "fail"])

    summary = svc.dashboard_summary(db_session, user_id=viewer.id, window_days=7)
    assert summary.kpis.health_score == 50.0
    assert [s.name for s in summary.suite_performance] == ["shared"]


def test_window_excludes_old_results(db_session: Any) -> None:
    alice = _user(db_session)
    suite = _suite(db_session, alice)
    _run_with_results(
        db_session, suite, run_status="succeeded", result_statuses=["pass"], age_days=1
    )
    _run_with_results(
        db_session, suite, run_status="failed", result_statuses=["critical"], age_days=40
    )

    summary = svc.dashboard_summary(db_session, user_id=alice.id, window_days=7)
    # The 40-day-old critical is outside the 7d window → score reflects only the pass.
    assert summary.kpis.health_score == 100.0
    assert summary.kpis.total_runs == 1


def test_run_trend_is_contiguous_and_zero_filled(db_session: Any) -> None:
    alice = _user(db_session)
    suite = _suite(db_session, alice)
    _run_with_results(
        db_session, suite, run_status="succeeded", result_statuses=["pass"], age_days=0
    )
    _run_with_results(db_session, suite, run_status="failed", result_statuses=["fail"], age_days=2)

    summary = svc.dashboard_summary(db_session, user_id=alice.id, window_days=7)
    trend = summary.trend

    # Contiguous daily axis, no gaps.
    days = [p.day for p in trend]
    assert days == sorted(days)
    for earlier, later in pairwise(days):
        assert (later - earlier).days == 1
    # Totals across the window match the runs created.
    assert sum(p.succeeded for p in trend) == 1
    assert sum(p.failed for p in trend) == 1


def test_suite_performance_uses_latest_run_worst_first(db_session: Any) -> None:
    alice = _user(db_session)
    healthy = _suite(db_session, alice, name="healthy")
    broken = _suite(db_session, alice, name="broken")
    # An older bad run on `healthy` that must be ignored in favour of the latest.
    _run_with_results(
        db_session, healthy, run_status="failed", result_statuses=["critical"], age_days=2
    )
    _run_with_results(
        db_session, healthy, run_status="succeeded", result_statuses=["pass", "pass"], age_days=0
    )
    _run_with_results(
        db_session, broken, run_status="failed", result_statuses=["fail", "critical"], age_days=0
    )

    summary = svc.dashboard_summary(db_session, user_id=alice.id, window_days=7)
    perf = {s.name: s for s in summary.suite_performance}

    assert perf["healthy"].score == 100.0  # latest run, not the old critical
    assert perf["healthy"].state == "optimal"
    assert perf["broken"].score == 25.0  # (1.0 + 2.0) / (2 * 2) = .75 → 25
    # Worst (lowest) first.
    assert [s.name for s in summary.suite_performance] == ["broken", "healthy"]
