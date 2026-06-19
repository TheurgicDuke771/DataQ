"""Tests for the run_suite Celery task orchestration.

No Postgres, no GX, no broker: a fake Session serves the run graph from memory,
``build_check_runner`` is monkeypatched to a fake CheckRunner, and the task
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
        self.table: str | None = None

    def run_checks(
        self, *, table: str, schema: str | None, checks: list[CheckSpec]
    ) -> SuiteOutcome:
        self.table = table
        return self._outcome


def _graph(n_checks: int = 1) -> tuple[Run, Suite, Connection, tuple[Check, ...]]:
    suite_id = uuid.uuid4()
    conn_id = uuid.uuid4()
    user_id = uuid.uuid4()
    run = Run(id=uuid.uuid4(), suite_id=suite_id, status="queued")
    suite = Suite(
        id=suite_id,
        name="probe",
        connection_id=conn_id,
        created_by=user_id,
        target={"table": "ORDERS"},
    )
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
        Check(
            id=uuid.uuid4(),
            suite_id=suite_id,
            name=f"c{i}",
            kind="expectation",
            expectation_type="x",
            config={},
        )
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
    monkeypatch.setattr(tasks, "build_check_runner", lambda **_kw: runner)

    status = tasks._run_suite(session, run_id=run.id)

    assert status == "succeeded"
    assert run.status == "succeeded"
    assert len(session.added) == 2


def test_run_suite_unity_catalog_threads_target_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Unity Catalog suite resolves its `catalog` (+ schema/table) from the
    target (#215) and threads it to the runner builder + the runner — the worker
    glue between `resolve_target` and `build_check_runner` that the registry/runner
    unit tests can't cover on their own. (UC's run path is otherwise the same
    in-process GX path as flat files; the live SQL-Warehouse read is the deferred
    smoke seam.)"""
    run, suite, connection, checks = _graph(1)
    connection.type = "unity_catalog"
    connection.config = {
        "workspace_url": "https://adb-1.2.azuredatabricks.net",
        "warehouse_id": "w",
    }
    suite.target = {"catalog": "main", "schema": "sales", "table": "orders"}
    session = FakeSession(run=run, suite=suite, connection=connection, checks=checks)
    runner = FakeRunner(SuiteOutcome(success=True, checks=[CheckOutcome("x", success=True)]))
    captured: dict[str, Any] = {}

    def _capture(**kwargs: Any) -> FakeRunner:
        captured.update(kwargs)
        return runner

    monkeypatch.setattr(tasks, "build_check_runner", _capture)

    status = tasks._run_suite(session, run_id=run.id)

    assert status == "succeeded"
    assert captured["conn_type"] == "unity_catalog"
    assert captured["catalog"] == "main"  # resolved from the suite target, not hardcoded
    assert runner.table == "orders"  # catalog.schema.table → table rides the runner slot
    assert len(session.added) == 1


# ───────────────────────── failure paths ───────────────────────────


def test_run_suite_missing_run_returns_not_found() -> None:
    session = FakeSession(run=None)
    status = tasks._run_suite(session, run_id=uuid.uuid4())
    assert status == "not_found"


def test_run_suite_missing_connection_marks_failed() -> None:
    run, suite, _conn, checks = _graph(1)
    session = FakeSession(run=run, suite=suite, connection=None, checks=checks)
    status = tasks._run_suite(session, run_id=run.id)
    assert status == "failed"
    assert run.status == "failed"
    assert run.finished_at is not None
    assert session.added == []


def test_run_suite_runner_build_failure_marks_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    run, suite, connection, checks = _graph(1)
    session = FakeSession(run=run, suite=suite, connection=connection, checks=checks)

    def _boom(**_kw: Any) -> Any:
        raise ValueError("Snowflake connection requires secret_ref for the password")

    monkeypatch.setattr(tasks, "build_check_runner", _boom)

    status = tasks._run_suite(session, run_id=run.id)
    assert status == "failed"
    assert run.status == "failed"


def test_run_suite_invalid_connection_config_marks_failed() -> None:
    """Real adapter path: a connection.config that fails SnowflakeConfig
    validation (missing required fields) drives the run to failed, not a crash."""
    run, suite, connection, checks = _graph(1)
    connection.config = {}  # missing account/user/database/schema/warehouse
    session = FakeSession(run=run, suite=suite, connection=connection, checks=checks)
    # build_check_runner is NOT monkeypatched here — real SnowflakeConfig
    # validation runs and raises, exercising the task's setup-failure handling.
    status = tasks._run_suite(session, run_id=run.id)
    assert status == "failed"
    assert run.status == "failed"


def test_run_suite_targetless_suite_marks_failed() -> None:
    """A suite with no `target` (#215) can't resolve a table → the run fails
    cleanly (suite_target_invalid) instead of running against an unknown table."""
    run, suite, connection, checks = _graph(1)
    suite.target = None
    session = FakeSession(run=run, suite=suite, connection=connection, checks=checks)
    status = tasks._run_suite(session, run_id=run.id)
    assert status == "failed"
    assert run.status == "failed"
    assert session.added == []  # never reached execution


# ───────────────────────── flat-file batch (A4) ────────────────────


def _flatfile_batch_graph() -> tuple[Run, Suite, Connection, tuple[Check, ...]]:
    run, suite, connection, checks = _graph(2)
    connection.type = "s3"
    connection.config = {"bucket": "b", "region": "r"}
    suite.target = {"prefix": "orders/", "pattern": r"orders_(\d+)\.csv"}
    return run, suite, connection, checks


def test_run_suite_batch_target_materialized_to_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """A flat-file batch target is materialized to a concrete path (live listing)
    and that resolved path is what the runner executes against."""
    run, suite, connection, checks = _flatfile_batch_graph()
    session = FakeSession(run=run, suite=suite, connection=connection, checks=checks)
    runner = FakeRunner(
        SuiteOutcome(
            success=True, checks=[CheckOutcome("x", success=True), CheckOutcome("x", success=True)]
        )
    )
    monkeypatch.setattr(tasks, "build_check_runner", lambda **_kw: runner)
    monkeypatch.setattr(
        tasks.run_target, "materialize_path", lambda *a, **k: "orders/orders_20260601.csv"
    )

    status = tasks._run_suite(session, run_id=run.id)

    assert status == "succeeded"
    assert runner.table == "orders/orders_20260601.csv"  # ran against the resolved batch file
    assert len(session.added) == 2


def test_run_suite_missing_batch_skips_without_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """A genuinely-absent batch (BatchNotFoundError) is a skip, not a failure
    (#122): every check gets a `skip` Result, the run succeeds, and the adapter
    is never executed."""
    run, suite, connection, checks = _flatfile_batch_graph()
    session = FakeSession(run=run, suite=suite, connection=connection, checks=checks)
    runner = FakeRunner(SuiteOutcome(success=True, checks=[]))
    monkeypatch.setattr(tasks, "build_check_runner", lambda **_kw: runner)

    def _missing(*_a: Any, **_k: Any) -> str:
        raise tasks.BatchNotFoundError("no files matched")

    monkeypatch.setattr(tasks.run_target, "materialize_path", _missing)

    status = tasks._run_suite(session, run_id=run.id)

    assert status == "succeeded"
    assert run.status == "succeeded"
    assert runner.table is None  # adapter never invoked
    assert len(session.added) == 2
    assert all(r.status == "skip" for r in session.added)
    assert all(r.observed_value == {"reason": "batch_not_found"} for r in session.added)


def test_run_suite_batch_listing_failure_marks_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transport/listing error during materialization (not a missing batch) is a
    real failure, not a skip."""
    run, suite, connection, checks = _flatfile_batch_graph()
    session = FakeSession(run=run, suite=suite, connection=connection, checks=checks)
    monkeypatch.setattr(tasks, "build_check_runner", lambda **_kw: FakeRunner(None))  # type: ignore[arg-type]

    def _boom(*_a: Any, **_k: Any) -> str:
        raise RuntimeError("S3 unreachable")

    monkeypatch.setattr(tasks.run_target, "materialize_path", _boom)

    status = tasks._run_suite(session, run_id=run.id)

    assert status == "failed"
    assert run.status == "failed"
    assert run.finished_at is not None
    assert session.added == []


# ───────────────────────── task wrapper ────────────────────────────


def test_task_wrapper_opens_and_closes_session(monkeypatch: pytest.MonkeyPatch) -> None:
    run, suite, connection, checks = _graph(1)
    session = FakeSession(run=run, suite=suite, connection=connection, checks=checks)
    runner = FakeRunner(SuiteOutcome(success=True, checks=[CheckOutcome("x", success=True)]))
    monkeypatch.setattr(tasks, "get_session", lambda: session)
    monkeypatch.setattr(tasks, "build_check_runner", lambda **_kw: runner)

    status = tasks.run_suite(str(run.id))

    assert status == "succeeded"
    assert session.closed is True
