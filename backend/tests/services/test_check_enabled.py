"""A switched-off check and the run that leaves it out (#2369), against real Postgres."""

from __future__ import annotations

import uuid
from typing import Any

from backend.app.db.models import Check, Connection, Result, Run, Suite, User
from backend.app.services import run_service


def _suite(db: Any) -> Suite:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex[:8]}@ex.com")
    db.add(owner)
    db.flush()
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}", type="postgres", env="dev", config={}, created_by=owner.id
    )
    db.add(conn)
    db.flush()
    suite = Suite(name=f"s-{uuid.uuid4().hex[:6]}", connection_id=conn.id, created_by=owner.id)
    db.add(suite)
    db.flush()
    return suite


def _check(db: Any, suite: Suite, name: str, *, enabled: bool = True, ordinal: int = 1) -> Check:
    check = Check(
        suite_id=suite.id,
        name=name,
        expectation_type="expect_x",
        config={},
        enabled=enabled,
        ordinal=ordinal,
    )
    db.add(check)
    db.flush()
    return check


def test_a_run_takes_only_the_checks_that_are_switched_on(db_session: Any) -> None:
    suite, other = _suite(db_session), _suite(db_session)
    on = _check(db_session, suite, "on")
    _check(db_session, suite, "off", enabled=False)
    _check(db_session, other, "another suite's check")

    assert [c.id for c in run_service.runnable_checks(db_session, suite.id)] == [on.id]


def test_a_suite_with_every_check_switched_off_has_nothing_to_run(db_session: Any) -> None:
    suite = _suite(db_session)
    _check(db_session, suite, "off", enabled=False)

    assert run_service.runnable_checks(db_session, suite.id) == []


def test_progress_leaves_a_switched_off_check_out_of_a_run_that_never_took_it(
    db_session: Any,
) -> None:
    """Otherwise the run reads `1 / 2` forever, waiting on a check it will never execute."""
    suite = _suite(db_session)
    on = _check(db_session, suite, "on", ordinal=1)
    _check(db_session, suite, "off", enabled=False, ordinal=2)
    run = Run(suite_id=suite.id, status="succeeded", triggered_by="test")
    db_session.add(run)
    db_session.flush()
    db_session.add(Result(run_id=run.id, check_id=on.id, status="pass"))
    db_session.flush()

    progress = run_service.get_run_progress(db_session, run)

    assert (progress.total_checks, progress.completed_checks) == (1, 1)
    assert [c.name for c in progress.checks] == ["on"]
    assert progress.batched_pending is False


def test_progress_keeps_a_result_recorded_before_the_check_was_switched_off(
    db_session: Any,
) -> None:
    """Switching a check off must not rewrite what an earlier run recorded."""
    suite = _suite(db_session)
    on = _check(db_session, suite, "on", ordinal=1)
    later_off = _check(db_session, suite, "later off", ordinal=2)
    run = Run(suite_id=suite.id, status="succeeded", triggered_by="test")
    db_session.add(run)
    db_session.flush()
    db_session.add_all(
        [
            Result(run_id=run.id, check_id=on.id, status="pass"),
            Result(run_id=run.id, check_id=later_off.id, status="fail"),
        ]
    )
    later_off.enabled = False
    db_session.flush()

    progress = run_service.get_run_progress(db_session, run)

    assert (progress.total_checks, progress.completed_checks) == (2, 2)
    assert progress.counts["fail"] == 1
