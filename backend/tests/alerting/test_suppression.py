"""Tests for alert suppression — honour per-check snoozes. DB-backed."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.app.alerting import suppression
from backend.app.db.models import Check, Connection, Result, Run, Suite, User

_NOW = datetime(2026, 6, 26, 12, 0, tzinfo=UTC)


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


def _check(db: Any, suite: Suite, *, snoozed_until: datetime | None = None) -> Check:
    check = Check(
        suite_id=suite.id,
        name=f"c-{uuid.uuid4().hex[:6]}",
        expectation_type="e",
        config={},
        alert_snoozed_until=snoozed_until,
    )
    db.add(check)
    db.flush()
    return check


def _run_with(
    db: Any, suite: Suite, results: list[tuple[Check, str]], *, status: str = "succeeded"
) -> Run:
    run = Run(suite_id=suite.id, status=status)
    db.add(run)
    db.flush()
    for check, st in results:
        db.add(Result(run_id=run.id, check_id=check.id, status=st))
    db.commit()
    return run


def test_no_failures_not_suppressed(db_session: Any) -> None:
    suite = _suite(db_session)
    chk = _check(db_session, suite)
    run = _run_with(db_session, suite, [(chk, "pass")])
    assert suppression.all_failures_snoozed(db_session, run, now=_NOW) is False


def test_live_failure_not_suppressed(db_session: Any) -> None:
    suite = _suite(db_session)
    chk = _check(db_session, suite)  # not snoozed
    run = _run_with(db_session, suite, [(chk, "fail")])
    assert suppression.all_failures_snoozed(db_session, run, now=_NOW) is False


def test_all_failures_snoozed_is_suppressed(db_session: Any) -> None:
    suite = _suite(db_session)
    chk = _check(db_session, suite, snoozed_until=_NOW + timedelta(hours=2))
    run = _run_with(db_session, suite, [(chk, "fail")])
    assert suppression.all_failures_snoozed(db_session, run, now=_NOW) is True


def test_partial_snooze_still_alerts(db_session: Any) -> None:
    suite = _suite(db_session)
    snoozed = _check(db_session, suite, snoozed_until=_NOW + timedelta(hours=2))
    live = _check(db_session, suite)  # not snoozed
    run = _run_with(db_session, suite, [(snoozed, "fail"), (live, "critical")])
    # One failing check is live → don't suppress.
    assert suppression.all_failures_snoozed(db_session, run, now=_NOW) is False


def test_expired_snooze_is_active_again(db_session: Any) -> None:
    suite = _suite(db_session)
    chk = _check(db_session, suite, snoozed_until=_NOW - timedelta(hours=1))  # in the past
    run = _run_with(db_session, suite, [(chk, "fail")])
    assert suppression.all_failures_snoozed(db_session, run, now=_NOW) is False


def test_snoozed_but_passing_is_not_suppressed(db_session: Any) -> None:
    # A snooze only matters for *failing* checks; a passing snoozed check leaves
    # the run with no failures, so there's nothing to suppress.
    suite = _suite(db_session)
    chk = _check(db_session, suite, snoozed_until=_NOW + timedelta(hours=2))
    run = _run_with(db_session, suite, [(chk, "pass")])
    assert suppression.all_failures_snoozed(db_session, run, now=_NOW) is False


def test_operational_failure_not_suppressed(db_session: Any) -> None:
    # An operational run failure has no per-check results → snooze can't apply.
    suite = _suite(db_session)
    run = _run_with(db_session, suite, [], status="failed")
    assert suppression.all_failures_snoozed(db_session, run, now=_NOW) is False
