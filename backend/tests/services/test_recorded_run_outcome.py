"""A run records its outcome as it succeeds, so deleting a check no longer rewrites it (#2348)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, text

from backend.app.core.auth import get_current_user
from backend.app.db.models import Check, Connection, Result, Run, Suite, User
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.mcp import server
from backend.app.services import run_service
from backend.tests.support.migrations import run_migration

_STATUSES = ("pass", "fail", "warn", "skip")


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _suite(db: Any) -> tuple[User, Suite]:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@acme.io")
    db.add(user)
    db.flush()
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "ab12345.eu-west-1"},
        secret_ref="kv-x",
        created_by=user.id,
    )
    db.add(conn)
    db.flush()
    suite = Suite(name="s", connection_id=conn.id, created_by=user.id, target={"table": "T"})
    db.add(suite)
    db.commit()
    return user, suite


def _running_run(
    db: Any, suite: Suite, statuses: tuple[str, ...] = _STATUSES
) -> tuple[Run, dict[str, Check]]:
    run = Run(suite_id=suite.id, status="running")
    db.add(run)
    db.flush()
    checks: dict[str, Check] = {}
    for status in statuses:
        check = Check(suite_id=suite.id, name=f"{status}-check", expectation_type="x", config={})
        db.add(check)
        db.flush()
        checks[status] = check
        db.add(Result(run_id=run.id, check_id=check.id, status=status))
    return run, checks


def _delete_check(db: Any, check: Check) -> None:
    db.execute(delete(Check).where(Check.id == check.id))  # the FK CASCADEs its results
    db.commit()
    db.expire_all()


def test_success_records_the_outcome(db_session: Any) -> None:
    _, suite = _suite(db_session)
    run, _ = _running_run(db_session, suite)

    assert run_service._mark_succeeded(db_session, run) is True
    db_session.expire_all()
    stored = db_session.get(Run, run.id)
    assert (
        stored.recorded_results_total,
        stored.recorded_checks_total,
        stored.recorded_checks_passed,
        stored.recorded_worst_severity,
    ) == (4, 3, 1, "fail")


def test_a_run_that_lost_the_race_to_a_cancel_records_nothing(db_session: Any) -> None:
    _, suite = _suite(db_session)
    run, _ = _running_run(db_session, suite)
    db_session.execute(text("UPDATE runs SET status = 'cancelled' WHERE id = :id"), {"id": run.id})

    assert run_service._mark_succeeded(db_session, run) is False
    db_session.expire_all()
    assert db_session.get(Run, run.id).recorded_results_total is None


def test_a_skipped_run_records_its_skips(db_session: Any) -> None:
    _, suite = _suite(db_session)
    run = Run(suite_id=suite.id, status="queued")
    check = Check(suite_id=suite.id, name="c", expectation_type="x", config={})
    db_session.add_all([run, check])
    db_session.commit()

    run_service.skip_run(db_session, run=run, checks=[check], reason="nothing to do")
    db_session.expire_all()
    stored = db_session.get(Run, run.id)
    assert (stored.recorded_results_total, stored.recorded_checks_total) == (1, 0)
    assert stored.recorded_worst_severity is None


def test_deleting_the_failing_check_leaves_the_outcome_and_counts_the_removal(
    db_session: Any,
) -> None:
    _, suite = _suite(db_session)
    run, checks = _running_run(db_session, suite)
    run_service._mark_succeeded(db_session, run)

    _delete_check(db_session, checks["fail"])

    assert run_service.check_outcome_counts(db_session, [run.id])[run.id] == (3, 1, "fail")
    assert run_service.removed_result_counts(db_session, [run.id]) == {run.id: 1}


def test_an_unrecorded_run_still_derives_its_outcome(db_session: Any) -> None:
    _, suite = _suite(db_session)
    run, checks = _running_run(db_session, suite)
    run.status = "succeeded"
    db_session.commit()

    _delete_check(db_session, checks["fail"])

    assert run_service.check_outcome_counts(db_session, [run.id])[run.id] == (2, 1, "warn")
    assert run_service.removed_result_counts(db_session, [run.id]) == {}


def test_the_runs_api_reports_the_recorded_outcome_and_the_removal(
    client: TestClient, db_session: Any
) -> None:
    user, suite = _suite(db_session)
    run, checks = _running_run(db_session, suite)
    run_service._mark_succeeded(db_session, run)
    _delete_check(db_session, checks["fail"])
    app.dependency_overrides[get_current_user] = lambda: user

    detail = client.get(f"/api/v1/runs/{run.id}").json()
    (listed,) = client.get("/api/v1/runs").json()

    for body in (detail, listed):
        assert (body["checks_total"], body["checks_passed"], body["worst_severity"]) == (
            3,
            1,
            "fail",
        )
        assert body["results_removed"] == 1
    assert len(detail["results"]) == 3


def test_the_mcp_run_tools_report_the_removal(db_session: Any, monkeypatch: Any) -> None:
    user, suite = _suite(db_session)
    run, checks = _running_run(db_session, suite)
    run_service._mark_succeeded(db_session, run)
    _delete_check(db_session, checks["fail"])
    monkeypatch.setattr(server, "get_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(server, "resolve_current_user", lambda _session: user)

    (listed,) = server.list_runs()["runs"]
    assert (listed["checks_total"], listed["worst_severity"], listed["results_removed"]) == (
        3,
        "fail",
        1,
    )
    assert server.get_run_results(str(run.id))["run"]["results_removed"] == 1


def test_the_backfill_records_finished_runs_only(db_session: Any) -> None:
    _, suite = _suite(db_session)
    finished, _ = _running_run(db_session, suite)
    finished.status = "succeeded"
    empty = Run(suite_id=suite.id, status="succeeded")
    in_flight, _ = _running_run(db_session, suite, ("pass",))
    db_session.add(empty)
    db_session.commit()

    run_migration(db_session, "ad8a8c3394b0_backfill_recorded_run_outcome.py", "upgrade")
    db_session.expire_all()

    def recorded(run: Run) -> tuple[Any, ...]:
        r = db_session.get(Run, run.id)
        return (
            r.recorded_results_total,
            r.recorded_checks_total,
            r.recorded_checks_passed,
            r.recorded_worst_severity,
        )

    assert recorded(finished) == (4, 3, 1, "fail")
    assert recorded(empty) == (0, 0, 0, None)
    assert recorded(in_flight) == (None, None, None, None)


def test_the_backfill_leaves_a_recorded_outcome_alone(db_session: Any) -> None:
    _, suite = _suite(db_session)
    run, checks = _running_run(db_session, suite)
    run_service._mark_succeeded(db_session, run)
    _delete_check(db_session, checks["fail"])

    run_migration(db_session, "ad8a8c3394b0_backfill_recorded_run_outcome.py", "upgrade")
    db_session.expire_all()

    assert db_session.get(Run, run.id).recorded_checks_total == 3
