"""Tests for the run/result persistence service.

No database or GX: a fake Session records what would be persisted, model
instances are built in memory with explicit ids, and a fake CheckRunner returns
canned outcomes (or raises). This keeps the service's lifecycle + mapping logic
under test independent of Postgres and Snowflake.
"""

import uuid
from decimal import Decimal

from backend.app.datasources.base import CheckOutcome, CheckSpec, SuiteOutcome
from backend.app.db.models import Check, Result, Run
from backend.app.services import run_service


class FakeSession:
    """Records add_all'd rows; counts commits/rollbacks. `add_all_raises` simulates
    a persistence failure (e.g. DB error) after the adapter has already run."""

    def __init__(self, *, add_all_raises: Exception | None = None) -> None:
        self.added: list[Result] = []
        self.commits = 0
        self.rollbacks = 0
        self._add_all_raises = add_all_raises

    def add_all(self, rows: list[Result]) -> None:
        if self._add_all_raises is not None:
            raise self._add_all_raises
        self.added.extend(rows)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class FakeRunner:
    def __init__(
        self, outcome: SuiteOutcome | None = None, raises: Exception | None = None
    ) -> None:
        self._outcome = outcome
        self._raises = raises
        self.called_with: dict[str, object] | None = None

    def run_checks(
        self, *, table: str, schema: str | None, checks: list[CheckSpec]
    ) -> SuiteOutcome:
        self.called_with = {"table": table, "schema": schema, "checks": checks}
        if self._raises is not None:
            raise self._raises
        assert self._outcome is not None
        return self._outcome


def _run() -> Run:
    return Run(id=uuid.uuid4(), suite_id=uuid.uuid4(), status="queued")


def _checks(n: int) -> list[Check]:
    return [
        Check(
            id=uuid.uuid4(),
            suite_id=uuid.uuid4(),
            name=f"c{i}",
            kind="expectation",
            expectation_type="x",
            config={},
        )
        for i in range(n)
    ]


# ───────────────────────── success path ────────────────────────────


def test_successful_run_persists_results_and_marks_succeeded() -> None:
    session = FakeSession()
    run = _run()
    checks = _checks(2)
    outcome = SuiteOutcome(
        success=False,  # a check failed, but the RUN still executed
        checks=[
            CheckOutcome("expect_a", success=True, observed_value={"observed_value": 5}),
            CheckOutcome(
                "expect_b",
                success=False,
                expected_value={"column": "id"},
                sample_failures={"unexpected_count": 1},
            ),
        ],
    )
    runner = FakeRunner(outcome=outcome)

    result = run_service.execute_run(
        session, run=run, checks=checks, runner=runner, table="ORDERS", schema="FIN"
    )

    assert result is run
    assert run.status == "succeeded"  # ran to completion despite a failed check
    assert run.started_at is not None and run.finished_at is not None
    assert len(session.added) == 2
    statuses = {r.check_id: r.status for r in session.added}
    assert statuses[checks[0].id] == "pass"
    assert statuses[checks[1].id] == "fail"
    # adapter received specs derived from the checks + the target table
    assert runner.called_with == {
        "table": "ORDERS",
        "schema": "FIN",
        "checks": [CheckSpec("x", {}), CheckSpec("x", {})],
    }


def test_results_link_to_run_and_check_ids() -> None:
    session = FakeSession()
    run = _run()
    checks = _checks(1)
    runner = FakeRunner(SuiteOutcome(success=True, checks=[CheckOutcome("x", success=True)]))

    run_service.execute_run(session, run=run, checks=checks, runner=runner, table="T")

    (row,) = session.added
    assert row.run_id == run.id
    assert row.check_id == checks[0].id


# ───────────────────────── NaN sanitisation ────────────────────────


def test_nan_in_sample_failures_is_sanitised_before_persist() -> None:
    session = FakeSession()
    runner = FakeRunner(
        SuiteOutcome(
            success=False,
            checks=[
                CheckOutcome(
                    "x",
                    success=False,
                    sample_failures={"partial_unexpected_list": [float("nan"), 2.0]},
                )
            ],
        )
    )

    run_service.execute_run(session, run=_run(), checks=_checks(1), runner=runner, table="T")

    (row,) = session.added
    assert row.sample_failures == {"partial_unexpected_list": [None, 2.0]}


# ───────────────────────── failure path ────────────────────────────


def test_runner_exception_marks_failed_and_persists_no_results() -> None:
    session = FakeSession()
    run = _run()
    runner = FakeRunner(raises=RuntimeError("cannot reach warehouse"))

    result = run_service.execute_run(session, run=run, checks=_checks(2), runner=runner, table="T")

    assert result.status == "failed"
    assert run.finished_at is not None
    assert session.added == []  # no half-written results


def test_persistence_failure_marks_failed_not_stuck_running() -> None:
    """If add_all/commit fails after a successful run, the run must reach a
    terminal 'failed' state (not stay 'running') and roll back partial inserts."""
    session = FakeSession(add_all_raises=RuntimeError("db connection lost"))
    run = _run()
    runner = FakeRunner(SuiteOutcome(success=True, checks=[CheckOutcome("x", success=True)]))

    result = run_service.execute_run(session, run=run, checks=_checks(1), runner=runner, table="T")

    assert result.status == "failed"
    assert run.finished_at is not None
    assert session.rollbacks == 1


def test_outcome_count_mismatch_marks_failed() -> None:
    """zip(strict=True): if the adapter returns the wrong number of outcomes."""
    session = FakeSession()
    runner = FakeRunner(SuiteOutcome(success=True, checks=[CheckOutcome("x", success=True)]))

    run_service.execute_run(session, run=_run(), checks=_checks(3), runner=runner, table="T")

    assert session.added == []


def test_empty_run_still_succeeds() -> None:
    session = FakeSession()
    run = _run()
    runner = FakeRunner(SuiteOutcome(success=True, checks=[]))

    run_service.execute_run(session, run=run, checks=[], runner=runner, table="T")

    assert run.status == "succeeded"
    assert session.added == []


def test_thresholds_derive_tier_and_persist_metric() -> None:
    """execute_run wires severity post-processing (ADR 0016): the unexpected-%
    is banded against the check's thresholds and persisted as metric_value."""
    session = FakeSession()
    run = _run()
    check = Check(
        id=uuid.uuid4(),
        suite_id=uuid.uuid4(),
        name="c",
        kind="expectation",
        expectation_type="x",
        config={},
        warn_threshold=Decimal("1"),
        fail_threshold=Decimal("5"),
        critical_threshold=Decimal("20"),
    )
    outcome = SuiteOutcome(
        success=False,
        checks=[CheckOutcome("x", success=False, sample_failures={"unexpected_percent": 7.5})],
    )
    run_service.execute_run(
        session, run=run, checks=[check], runner=FakeRunner(outcome=outcome), table="T"
    )

    persisted = session.added[0]
    assert persisted.status == "fail"  # 7.5 ≥ fail(5), < critical(20)
    assert persisted.metric_value == Decimal("7.5")


def test_non_expectation_kind_fails_run_without_invoking_runner() -> None:
    """A reserved (non-expectation) check kind has no runner in v1 (ADR 0012):
    the run fails loudly rather than silently feeding it to GX, and the adapter
    is never called."""
    session = FakeSession()
    run = _run()
    freshness = Check(
        id=uuid.uuid4(),
        suite_id=uuid.uuid4(),
        name="stale_load",
        kind="freshness",  # constraint-valid, but no runner in v1
        expectation_type="",
        config={"interval_hours": 24},
    )
    runner = FakeRunner(SuiteOutcome(success=True, checks=[]))

    result = run_service.execute_run(session, run=run, checks=[freshness], runner=runner, table="T")

    assert result.status == "failed"  # NotImplementedError → terminal 'failed'
    assert runner.called_with is None  # dispatch short-circuited before the adapter
    assert session.added == []  # nothing persisted
    assert session.rollbacks == 1
