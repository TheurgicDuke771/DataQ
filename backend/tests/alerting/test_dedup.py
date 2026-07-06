"""Tests for alert dedup — fire on first failure, suppress unchanged repeats.

DB-backed: builds a suite with a sequence of runs and asserts which runs are
"duplicate" (suppress) vs new (fire). Skips without TEST_DATABASE_URL.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.app.alerting import dedup
from backend.app.db.models import Check, Connection, Result, Run, Suite, User


def _suite(db: Any) -> Suite:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:6]}@x.io")
    db.add(owner)
    db.flush()
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
    suite = Suite(name="s", connection_id=conn.id, created_by=owner.id, target={"table": "T"})
    db.add(suite)
    db.flush()
    return suite


def _check(db: Any, suite: Suite) -> Check:
    check = Check(
        suite_id=suite.id, name=f"c-{uuid.uuid4().hex[:6]}", expectation_type="e", config={}
    )
    db.add(check)
    db.flush()
    return check


# Monotonic run timestamps so "previous run" is unambiguous.
_BASE = datetime(2026, 6, 26, 0, 0, tzinfo=UTC)


def _run(db: Any, suite: Suite, *, seq: int, status: str = "succeeded") -> Run:
    run = Run(suite_id=suite.id, status=status, created_at=_BASE + timedelta(minutes=seq))
    db.add(run)
    db.flush()
    return run


def _result(db: Any, run: Run, check: Check, status: str) -> None:
    db.add(Result(run_id=run.id, check_id=check.id, status=status))
    db.flush()


def test_rank_derives_from_shared_failing_tiers() -> None:
    """#386: dedup's severity ranks are the one shared severity order, not an
    independent copy — so adding or reordering a tier in ``base.FAILING_TIERS``
    can't silently diverge dedup from routing/suppression."""
    from backend.app.alerting.base import FAILING_TIERS

    # Same tiers, same order, ranked worst-last from the single source.
    assert dedup._RANK == {tier: rank for rank, tier in enumerate(FAILING_TIERS, start=1)}
    assert tuple(dedup._RANK) == FAILING_TIERS
    # The operational-failure sentinel is ranked at `fail`, from that same source.
    assert dedup._OPERATIONAL_RANK == dedup._RANK["fail"]


def test_first_failure_fires(db_session: Any) -> None:
    suite = _suite(db_session)
    chk = _check(db_session, suite)
    run = _run(db_session, suite, seq=1)
    _result(db_session, run, chk, "fail")
    db_session.commit()
    # No previous terminal run → not a duplicate → fire.
    assert dedup.is_duplicate_alert(db_session, run) is False


def test_unchanged_repeat_is_suppressed(db_session: Any) -> None:
    suite = _suite(db_session)
    chk = _check(db_session, suite)
    r1 = _run(db_session, suite, seq=1)
    _result(db_session, r1, chk, "fail")
    r2 = _run(db_session, suite, seq=2)
    _result(db_session, r2, chk, "fail")
    db_session.commit()
    # Same check still failing at the same severity → duplicate → suppress.
    assert dedup.is_duplicate_alert(db_session, r2) is True


def test_escalation_fires(db_session: Any) -> None:
    suite = _suite(db_session)
    chk = _check(db_session, suite)
    r1 = _run(db_session, suite, seq=1)
    _result(db_session, r1, chk, "fail")
    r2 = _run(db_session, suite, seq=2)
    _result(db_session, r2, chk, "critical")
    db_session.commit()
    # fail → critical is worse → fire.
    assert dedup.is_duplicate_alert(db_session, r2) is False


def test_new_failing_check_fires(db_session: Any) -> None:
    suite = _suite(db_session)
    a, b = _check(db_session, suite), _check(db_session, suite)
    r1 = _run(db_session, suite, seq=1)
    _result(db_session, r1, a, "fail")
    _result(db_session, r1, b, "pass")
    r2 = _run(db_session, suite, seq=2)
    _result(db_session, r2, a, "fail")
    _result(db_session, r2, b, "fail")  # b newly fails at the same overall worst
    db_session.commit()
    # b is a *new* failing check even though the worst severity is unchanged.
    assert dedup.is_duplicate_alert(db_session, r2) is False


def test_recovery_then_failure_refires(db_session: Any) -> None:
    suite = _suite(db_session)
    chk = _check(db_session, suite)
    r1 = _run(db_session, suite, seq=1)
    _result(db_session, r1, chk, "fail")
    r2 = _run(db_session, suite, seq=2)  # recovered
    _result(db_session, r2, chk, "pass")
    r3 = _run(db_session, suite, seq=3)  # fails again
    _result(db_session, r3, chk, "fail")
    db_session.commit()
    # The previous run (r2) was clean → this failure is new again → fire.
    assert dedup.is_duplicate_alert(db_session, r3) is False


def test_de_escalation_is_suppressed(db_session: Any) -> None:
    suite = _suite(db_session)
    chk = _check(db_session, suite)
    r1 = _run(db_session, suite, seq=1)
    _result(db_session, r1, chk, "critical")
    r2 = _run(db_session, suite, seq=2)
    _result(db_session, r2, chk, "warn")
    db_session.commit()
    # Improving (critical → warn) is not a new alert → suppress.
    assert dedup.is_duplicate_alert(db_session, r2) is True


def test_clean_run_is_never_a_duplicate(db_session: Any) -> None:
    suite = _suite(db_session)
    chk = _check(db_session, suite)
    r1 = _run(db_session, suite, seq=1)
    _result(db_session, r1, chk, "fail")
    r2 = _run(db_session, suite, seq=2)
    _result(db_session, r2, chk, "pass")
    db_session.commit()
    # A passing run isn't an alert at all → not a duplicate (publisher no-ops it).
    assert dedup.is_duplicate_alert(db_session, r2) is False


def test_same_timestamp_prior_run_is_the_baseline(db_session: Any) -> None:
    # Two runs sharing a created_at (a scheduling burst): the lower-id one is the
    # baseline, so the same failure on the later run still dedups (strict `<` on
    # created_at alone would have missed it and re-fired).
    suite = _suite(db_session)
    chk = _check(db_session, suite)
    when = _BASE + timedelta(minutes=1)
    r1 = Run(suite_id=suite.id, status="succeeded", created_at=when)
    db_session.add(r1)
    db_session.flush()
    r2 = Run(suite_id=suite.id, status="succeeded", created_at=when)
    db_session.add(r2)
    db_session.flush()
    _result(db_session, r1, chk, "fail")
    _result(db_session, r2, chk, "fail")
    db_session.commit()
    # UUID PKs are random, so the (created_at, id) total order — not insert order —
    # decides which is "prior": the higher-id run dedups against the lower-id one,
    # and exactly one alert fires for the burst pair.
    later, earlier = (r2, r1) if r2.id > r1.id else (r1, r2)
    assert dedup.is_duplicate_alert(db_session, later) is True
    assert dedup.is_duplicate_alert(db_session, earlier) is False  # first → fires


def test_repeated_operational_failure_is_suppressed(db_session: Any) -> None:
    suite = _suite(db_session)
    r1 = _run(db_session, suite, seq=1, status="failed")  # no result rows
    r2 = _run(db_session, suite, seq=2, status="failed")
    db_session.commit()
    assert dedup.is_duplicate_alert(db_session, r1) is False  # first
    assert dedup.is_duplicate_alert(db_session, r2) is True  # repeat
