"""Tests for the run_suite Celery task orchestration.

No Postgres, no GX, no broker: a fake Session serves the run graph from memory,
``build_snowflake_runner`` is monkeypatched to a fake CheckRunner, and the task
core (`_run_suite`) is called directly. Real-DB integration coverage is a Week 8
item (Postgres test fixtures).
"""

import uuid
from typing import Any

import pytest

from backend.app.datasources.base import CheckOutcome, CheckSpec, SuiteOutcome
from backend.app.db.models import Check, Connection, Run, Suite
from backend.app.worker import tasks


class FakeSession:
    def __init__(
        self,
        *,
        run: Run | None = None,
        suite: Suite | None = None,
        connection: Connection | None = None,
        checks: tuple[Check, ...] = (),
    ) -> None:
        self._objs: dict[type, Any] = {Run: run, Suite: suite, Connection: connection}
        self._checks = list(checks)
        self.added: list[Any] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def get(self, model: type, pk: Any) -> Any:
        return self._objs.get(model)

    def scalars(self, _stmt: Any) -> Any:
        return iter(self._checks)

    def add_all(self, rows: list[Any]) -> None:
        self.added.extend(rows)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


class FakeRunner:
    def __init__(self, outcome: SuiteOutcome) -> None:
        self._outcome = outcome

    def run_checks(
        self, *, table: str, schema: str | None, checks: list[CheckSpec]
    ) -> SuiteOutcome:
        return self._outcome


def _graph(n_checks: int = 1) -> tuple[Run, Suite, Connection, tuple[Check, ...]]:
    suite_id = uuid.uuid4()
    conn_id = uuid.uuid4()
    user_id = uuid.uuid4()
    run = Run(id=uuid.uuid4(), suite_id=suite_id, status="queued")
    suite = Suite(id=suite_id, name="probe", connection_id=conn_id, created_by=user_id)
    connection = Connection(
        id=conn_id,
        name="probe-sf",
        type="snowflake",
        env="dev",
        config={"account": "a"},
        secret_ref="snowflake-dev",
        created_by=user_id,
    )
    checks = tuple(
        Check(id=uuid.uuid4(), suite_id=suite_id, name=f"c{i}", expectation_type="x", config={})
        for i in range(n_checks)
    )
    return run, suite, connection, checks


# ───────────────────────── happy path ──────────────────────────────


def test_run_suite_executes_and_persists(monkeypatch: pytest.MonkeyPatch) -> None:
    run, suite, connection, checks = _graph(2)
    session = FakeSession(run=run, suite=suite, connection=connection, checks=checks)
    runner = FakeRunner(
        SuiteOutcome(
            success=True,
            checks=[CheckOutcome("x", success=True), CheckOutcome("x", success=True)],
        )
    )
    monkeypatch.setattr(tasks, "build_snowflake_runner", lambda **_kw: runner)

    status = tasks._run_suite(session, run_id=run.id, table="ORDERS", schema=None)

    assert status == "succeeded"
    assert run.status == "succeeded"
    assert len(session.added) == 2


# ───────────────────────── failure paths ───────────────────────────


def test_run_suite_missing_run_returns_not_found() -> None:
    session = FakeSession(run=None)
    status = tasks._run_suite(session, run_id=uuid.uuid4(), table="T", schema=None)
    assert status == "not_found"


def test_run_suite_missing_connection_marks_failed() -> None:
    run, suite, _conn, checks = _graph(1)
    session = FakeSession(run=run, suite=suite, connection=None, checks=checks)
    status = tasks._run_suite(session, run_id=run.id, table="T", schema=None)
    assert status == "failed"
    assert run.status == "failed"
    assert run.finished_at is not None
    assert session.added == []


def test_run_suite_runner_build_failure_marks_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    run, suite, connection, checks = _graph(1)
    session = FakeSession(run=run, suite=suite, connection=connection, checks=checks)

    def _boom(**_kw: Any) -> Any:
        raise ValueError("Snowflake connection requires secret_ref for the password")

    monkeypatch.setattr(tasks, "build_snowflake_runner", _boom)

    status = tasks._run_suite(session, run_id=run.id, table="T", schema=None)
    assert status == "failed"
    assert run.status == "failed"


def test_run_suite_invalid_connection_config_marks_failed() -> None:
    """Real adapter path: a connection.config that fails SnowflakeConfig
    validation (missing required fields) drives the run to failed, not a crash."""
    run, suite, connection, checks = _graph(1)
    connection.config = {}  # missing account/user/database/schema/warehouse
    session = FakeSession(run=run, suite=suite, connection=connection, checks=checks)
    # build_snowflake_runner is NOT monkeypatched here — real SnowflakeConfig
    # validation runs and raises, exercising the task's setup-failure handling.
    status = tasks._run_suite(session, run_id=run.id, table="T", schema=None)
    assert status == "failed"
    assert run.status == "failed"


# ───────────────────────── task wrapper ────────────────────────────


def test_task_wrapper_opens_and_closes_session(monkeypatch: pytest.MonkeyPatch) -> None:
    run, suite, connection, checks = _graph(1)
    session = FakeSession(run=run, suite=suite, connection=connection, checks=checks)
    runner = FakeRunner(SuiteOutcome(success=True, checks=[CheckOutcome("x", success=True)]))
    monkeypatch.setattr(tasks, "get_session", lambda: session)
    monkeypatch.setattr(tasks, "build_snowflake_runner", lambda **_kw: runner)

    status = tasks.run_suite(str(run.id), "ORDERS")

    assert status == "succeeded"
    assert session.closed is True
