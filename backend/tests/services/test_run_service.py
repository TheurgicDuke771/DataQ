"""Tests for the run/result persistence service.

No database or GX: a fake Session records what would be persisted, model
instances are built in memory with explicit ids, and a fake CheckRunner returns
canned outcomes (or raises). This keeps the service's lifecycle + mapping logic
under test independent of Postgres and Snowflake.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy import Update
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import Session

from backend.app.datasources.base import (
    SAMPLE_ROW_CAP,
    CheckOutcome,
    CheckRunner,
    CheckSpec,
    SuiteOutcome,
)
from backend.app.datasources.monitors import MONITOR_KINDS
from backend.app.datasources.sampling import ScanTooLargeError
from backend.app.db.models import Check, Result, Run
from backend.app.services import run_service
from backend.tests.support.run_phases import collect_outcomes


class FakeSession:
    """Records add_all'd rows; counts commits/rollbacks. `add_all_raises` simulates
    a persistence failure (e.g. DB error) after the adapter has already run.

    Since #318 `execute_run` commits **per execution phase**, so this double has to
    tell a staged row from a committed one — a rollback discards only the former.
    `added` is the combined "what a reader would see" view the assertions use;
    `_staged_from` marks where the uncommitted tail starts.

    It also serves the two Core statements the run path now issues: the
    `DELETE FROM results` that `discard_run_results` uses to clear *committed*
    phases (a rollback alone can no longer reach them), and the conditional
    `UPDATE runs SET status='succeeded' WHERE status='running'` that makes a
    concurrent cancel win instead of being overwritten. `cancelled` makes the
    UPDATE report zero rows — the double must agree with `scalar` about the run's
    state, or it describes a row the DB could not hold.
    """

    def __init__(
        self, *, add_all_raises: Exception | None = None, refresh_status: str | None = None
    ) -> None:
        self.added: list[Result] = []
        self.commits = 0
        self.rollbacks = 0
        self.deletes = 0
        self._staged_from = 0
        self._add_all_raises = add_all_raises
        # When set, this is what a concurrent session has committed as the run's
        # status — read back by `scalar` (the cancel poll) and by `refresh`.
        self._refresh_status = refresh_status

    def add_all(self, rows: list[Result]) -> None:
        if self._add_all_raises is not None:
            raise self._add_all_raises
        self.added.extend(rows)

    def commit(self) -> None:
        self.commits += 1
        self._staged_from = len(self.added)

    def rollback(self) -> None:
        self.rollbacks += 1
        # Discard staged-but-uncommitted rows, like a real rollback — committed
        # phases survive it, which is exactly why `discard_run_results` DELETEs.
        del self.added[self._staged_from :]

    def scalar(self, statement: object) -> Any:
        """`_cancelled_mid_run` reads the status column rather than refreshing."""
        return self._refresh_status or "running"

    def execute(self, statement: object) -> Any:
        """The DELETE (discard) and the UPDATE (guarded succeeded-flip)."""
        if isinstance(statement, Update):
            # The flip is conditional on `status = 'running'`; a committed cancel
            # means it matches nothing.
            return SimpleNamespace(rowcount=0 if self._refresh_status == "cancelled" else 1)
        self.deletes += 1
        self.added.clear()
        self._staged_from = 0
        return SimpleNamespace(rowcount=0)

    def refresh(self, obj: object) -> None:
        if self._refresh_status is not None:
            obj.status = self._refresh_status  # type: ignore[attr-defined]


def _sess(session: FakeSession) -> Session:
    """Type a ``FakeSession`` test double as ``Session`` for the service signatures
    (the tests still hold the ``FakeSession`` ref for their `.added`/`.commits`
    assertions; only the call arg is cast)."""
    return cast(Session, session)


class FakeRunner:
    def __init__(
        self, outcome: SuiteOutcome | None = None, raises: Exception | None = None
    ) -> None:
        self._outcome = outcome
        self._raises = raises
        self.called_with: dict[str, object] | None = None

    def run_checks(
        self,
        *,
        table: str,
        schema: str | None,
        checks: list[CheckSpec],
        index_columns: list[str] | None = None,
    ) -> SuiteOutcome:
        self.called_with = {
            "table": table,
            "schema": schema,
            "checks": checks,
            "index_columns": index_columns,
        }
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


def _monitor_check(kind: str, config: dict[str, object]) -> Check:
    return Check(
        id=uuid.uuid4(),
        suite_id=uuid.uuid4(),
        name=kind,
        kind=kind,
        expectation_type="",
        config=config,
    )


class FakeMonitorRunner:
    """A SQL-datasource-like runner that handles both expectation (run_checks) and
    monitor (run_monitors) kinds — so the kind-dispatch can route to each."""

    supported_monitor_kinds = frozenset(MONITOR_KINDS)  # the #429 capability the gate reads

    def __init__(
        self, *, check_outcomes: list[CheckOutcome], monitor_outcomes: list[CheckOutcome]
    ) -> None:
        self._check_outcomes = check_outcomes
        self._monitor_outcomes = monitor_outcomes
        self.monitors_called_with: list[object] | None = None

    def run_checks(
        self,
        *,
        table: str,
        schema: str | None,
        checks: list[CheckSpec],
        index_columns: list[str] | None = None,
    ) -> SuiteOutcome:
        return SuiteOutcome(success=True, checks=self._check_outcomes)

    def run_monitors(
        self, *, table: str, schema: str | None, monitors: list[object]
    ) -> list[CheckOutcome]:
        self.monitors_called_with = monitors
        return self._monitor_outcomes


# ───────────────────── kind dispatch (_run_outcome_phases) ───────────


def test_run_outcomes_routes_by_kind_and_keeps_check_order() -> None:
    # checks interleaved: [expectation, freshness, expectation] — outcomes must come
    # back in that same order (so they zip 1:1 onto the result rows).
    checks = [_checks(1)[0], _monitor_check("freshness", {"column": "ts"}), _checks(1)[0]]
    runner = FakeMonitorRunner(
        check_outcomes=[CheckOutcome("e1", success=True), CheckOutcome("e2", success=True)],
        monitor_outcomes=[CheckOutcome("monitor:freshness", success=True, metric_value=5.0)],
    )

    outcomes = collect_outcomes(cast(CheckRunner, runner), table="T", schema=None, checks=checks)

    assert [o.expectation_type for o in outcomes] == ["e1", "monitor:freshness", "e2"]
    assert runner.monitors_called_with is not None and len(runner.monitors_called_with) == 1


def test_run_outcomes_monitor_on_non_sql_runner_raises() -> None:
    # FakeRunner advertises no monitor capability → monitor check rejected
    # (freshness/volume need a monitor-capable datasource).
    runner = FakeRunner(outcome=SuiteOutcome(success=True, checks=[]))
    with pytest.raises(NotImplementedError, match="monitor"):
        collect_outcomes(
            runner,
            table="T",
            schema=None,
            checks=[_monitor_check("volume", {"min_rows": 1, "max_rows": 9})],
        )


def test_run_outcomes_rejects_runner_with_unrelated_run_monitors() -> None:
    # The #429 AC: a runner that merely HAS a method named run_monitors (which an
    # isinstance against the runtime_checkable Protocol would have accepted) but
    # advertises no capability is rejected CLEANLY at the gate — never a TypeError
    # from calling an unrelated method.
    class _Impostor(FakeRunner):
        def run_monitors(self) -> None:  # unrelated signature — calling it would TypeError
            raise AssertionError("the gate must reject before calling this")

    runner = _Impostor(outcome=SuiteOutcome(success=True, checks=[]))
    with pytest.raises(NotImplementedError, match="does not support monitor kind"):
        collect_outcomes(
            runner,
            table="T",
            schema=None,
            checks=[_monitor_check("volume", {"min_rows": 1, "max_rows": 9})],
        )


def test_run_outcomes_rejects_capability_without_implementation() -> None:
    # The mirror hole of the old isinstance gate (#880 review): advertising
    # kinds without run_monitors must reject as cleanly as the reverse — never
    # an AttributeError at the call site.
    class _AllTalk(FakeRunner):
        supported_monitor_kinds = frozenset(MONITOR_KINDS)  # no run_monitors at all

    runner = _AllTalk(outcome=SuiteOutcome(success=True, checks=[]))
    with pytest.raises(NotImplementedError, match="capability and implementation drifted"):
        collect_outcomes(
            runner,
            table="T",
            schema=None,
            checks=[_monitor_check("volume", {"min_rows": 1, "max_rows": 9})],
        )


def test_outcome_phases_shape_and_order() -> None:
    """The phase shape #318's incremental progress rests on, asserted directly.

    Two independent limits are encoded here and both are load-bearing:

    * **granularity** — per check for the executor-driven `comparison`; ONE phase
      for the whole expectation batch (GX validates atomically) and ONE for the
      scalar monitors (`run_monitors` loads the frame once);
    * **durability** — the stateful kinds are yielded LAST and `publishable=False`,
      because their executors write `monitor_baselines` through the caller's
      session. Committing them early would make a failed run's baseline permanent.
      Last matters as much as unpublishable: a commit is transaction-wide, so
      anything yielded after them would flush those writes anyway.

    A future change that splits the GX batch, or that lets a stateful phase
    publish, should update this test deliberately rather than discover it in
    production.
    """
    checks = [
        _monitor_check("schema_drift", {}),
        _checks(1)[0],
        _monitor_check("freshness", {"column": "ts"}),
        _checks(1)[0],
        _monitor_check("anomaly", {"column": "amt"}),
        _monitor_check("volume", {"min_rows": 1, "max_rows": 9}),
    ]
    runner = FakeMonitorRunner(
        check_outcomes=[CheckOutcome("e1", success=True), CheckOutcome("e2", success=True)],
        monitor_outcomes=[
            CheckOutcome("monitor:freshness", success=True),
            CheckOutcome("monitor:volume", success=True),
        ],
    )

    phases = list(
        run_service._run_outcome_phases(
            cast(CheckRunner, runner),
            table="T",
            schema=None,
            checks=checks,
            stateful_monitor_executor=lambda c: CheckOutcome(c.expectation_type, success=True),
        )
    )

    assert [[i for i, _ in p.resolved] for p in phases] == [
        [1, 3],  # the atomic GX expectation batch
        [2, 5],  # the shared-frame scalar monitors
        [0],  # schema_drift — last, and unpublishable
        [4],  # anomaly — same
    ]
    assert [p.publishable for p in phases] == [True, True, False, False]
    # Every check appears exactly once across the phases, in no more than one.
    assert sorted(i for p in phases for i, _ in p.resolved) == list(range(len(checks)))


def test_run_outcomes_stateful_kind_routes_to_injected_executor() -> None:
    # schema_drift (#592) never reaches runner.run_monitors — it goes to the
    # session-aware executor the worker injects (the comparison pattern).
    runner = FakeRunner(outcome=SuiteOutcome(success=True, checks=[]))
    seen: list[str] = []

    def executor(check: Check) -> CheckOutcome:
        seen.append(check.kind)
        return CheckOutcome("monitor:schema_drift", success=True, metric_value=0.0)

    [outcome] = collect_outcomes(
        runner,
        table="T",
        schema=None,
        checks=[_monitor_check("schema_drift", {})],
        stateful_monitor_executor=executor,
    )
    assert seen == ["schema_drift"]
    assert outcome.metric_value == 0.0


def test_run_outcomes_stateful_kind_without_executor_errors_per_check() -> None:
    # No executor supplied (a caller that can't reach the baseline store) → the
    # CHECK errors; siblings still run (#122), the run never raises.
    runner = FakeRunner(outcome=SuiteOutcome(success=True, checks=[]))
    [outcome] = collect_outcomes(
        runner,
        table="T",
        schema=None,
        checks=[_monitor_check("schema_drift", {})],
    )
    assert outcome.errored is True
    assert outcome.error_message is not None
    assert "baseline-diff run path" in outcome.error_message


def test_run_outcomes_gate_is_per_kind_not_per_runner() -> None:
    # Capability is a SET of kinds (#429 altitude note): a runner supporting only
    # freshness must reject a volume check by NAME, so stateful kinds (#592/#593)
    # can land on some runners before others without re-entangling the seams.
    class _FreshnessOnly(FakeMonitorRunner):
        supported_monitor_kinds = frozenset({"freshness"})

    runner = _FreshnessOnly(check_outcomes=[], monitor_outcomes=[])
    with pytest.raises(NotImplementedError, match="volume"):
        collect_outcomes(
            cast(CheckRunner, runner),
            table="T",
            schema=None,
            checks=[_monitor_check("volume", {"min_rows": 1, "max_rows": 9})],
        )


def test_run_outcomes_unsupported_kind_raises() -> None:
    # Every kind in CHECK_KINDS now has a run path (#593 closed the last reserved
    # one), so this pins the guard itself with a kind that exists nowhere: a check
    # row whose kind the dispatcher cannot place must raise, never silently run as
    # an expectation or vanish from the outcome list.
    runner = FakeRunner(outcome=SuiteOutcome(success=True, checks=[]))
    with pytest.raises(NotImplementedError, match="not_a_kind"):
        collect_outcomes(runner, table="T", schema=None, checks=[_monitor_check("not_a_kind", {})])


def test_run_outcomes_comparison_errors_without_failing_siblings() -> None:
    # ADR 0015: a comparison check on a caller that supplies no executor must
    # yield a per-check operational `error` outcome — in order — while its
    # expectation siblings still evaluate normally (#122).
    comparison = _monitor_check("comparison", {"source": {"table": "T"}, "keys": ["id"]})
    comparison.expectation_type = "comparison:records"
    checks = [_checks(1)[0], comparison, _checks(1)[0]]
    runner = FakeRunner(
        outcome=SuiteOutcome(
            success=True,
            checks=[CheckOutcome("e1", success=True), CheckOutcome("e2", success=True)],
        )
    )

    outcomes = collect_outcomes(runner, table="T", schema=None, checks=checks)

    assert [o.expectation_type for o in outcomes] == ["e1", "comparison:records", "e2"]
    assert outcomes[1].errored and not outcomes[1].success
    assert outcomes[1].error_message is not None and "executor" in outcomes[1].error_message
    assert outcomes[0].success and outcomes[2].success


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
        _sess(session), run=run, checks=checks, runner=runner, table="ORDERS", schema="FIN"
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
        "index_columns": None,
    }


def test_results_link_to_run_and_check_ids() -> None:
    session = FakeSession()
    run = _run()
    checks = _checks(1)
    runner = FakeRunner(SuiteOutcome(success=True, checks=[CheckOutcome("x", success=True)]))

    run_service.execute_run(_sess(session), run=run, checks=checks, runner=runner, table="T")

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

    run_service.execute_run(_sess(session), run=_run(), checks=_checks(1), runner=runner, table="T")

    (row,) = session.added
    assert row.sample_failures == {"partial_unexpected_list": [None, 2.0]}


# ───────────────────────── failure path ────────────────────────────


def test_runner_exception_marks_failed_and_persists_no_results() -> None:
    session = FakeSession()
    run = _run()
    runner = FakeRunner(raises=RuntimeError("cannot reach warehouse"))

    result = run_service.execute_run(
        _sess(session), run=run, checks=_checks(2), runner=runner, table="T"
    )

    assert result.status == "failed"
    assert run.finished_at is not None
    assert session.added == []  # no half-written results
    # A redaction-safe reason is recorded (#605) — a fixed classified message, not
    # the raw exception text (which could carry DSN/credential fragments).
    assert run.failure_reason
    assert "cannot reach warehouse" not in run.failure_reason


def test_success_clears_a_stale_failure_reason() -> None:
    """A reaped-then-completed run must not surface as succeeded-with-a-reason:
    the success path clears any failure_reason a prior reap stamped (#605)."""
    session = FakeSession()
    run = _run()
    run.failure_reason = "The run did not complete in time and was marked failed."
    runner = FakeRunner(SuiteOutcome(success=True, checks=[CheckOutcome("x", success=True)]))

    run_service.execute_run(_sess(session), run=run, checks=_checks(1), runner=runner, table="T")

    assert run.status == "succeeded"
    assert run.failure_reason is None


def test_persistence_failure_marks_failed_not_stuck_running() -> None:
    """If add_all/commit fails after a successful run, the run must reach a
    terminal 'failed' state (not stay 'running') and roll back partial inserts."""
    session = FakeSession(add_all_raises=RuntimeError("db connection lost"))
    run = _run()
    runner = FakeRunner(SuiteOutcome(success=True, checks=[CheckOutcome("x", success=True)]))

    result = run_service.execute_run(
        _sess(session), run=run, checks=_checks(1), runner=runner, table="T"
    )

    assert result.status == "failed"
    assert run.finished_at is not None
    assert session.rollbacks == 1


def test_outcome_count_mismatch_marks_failed() -> None:
    """zip(strict=True): if the adapter returns the wrong number of outcomes."""
    session = FakeSession()
    runner = FakeRunner(SuiteOutcome(success=True, checks=[CheckOutcome("x", success=True)]))

    run_service.execute_run(_sess(session), run=_run(), checks=_checks(3), runner=runner, table="T")

    assert session.added == []


def test_empty_run_still_succeeds() -> None:
    session = FakeSession()
    run = _run()
    runner = FakeRunner(SuiteOutcome(success=True, checks=[]))

    run_service.execute_run(_sess(session), run=run, checks=[], runner=runner, table="T")

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
        _sess(session), run=run, checks=[check], runner=FakeRunner(outcome=outcome), table="T"
    )

    persisted = session.added[0]
    assert persisted.status == "fail"  # 7.5 ≥ fail(5), < critical(20)
    assert persisted.metric_value == Decimal("7.5")


def test_errored_check_maps_to_error_status_without_failing_siblings() -> None:
    """A check the runner could not evaluate (`outcome.errored`) is an operational
    `error` result (#122) — no severity, no metric — and never fails its siblings:
    the sibling still maps to its tier and the RUN still succeeds."""
    session = FakeSession()
    run = _run()
    checks = _checks(2)
    outcome = SuiteOutcome(
        success=False,  # GX marks the suite failed because one check raised
        checks=[
            CheckOutcome(
                "expect_bad",
                success=False,
                errored=True,
                error_message='Error: The column "nope" in BatchData does not exist.',
            ),
            CheckOutcome("expect_ok", success=True, observed_value={"observed_value": 3}),
        ],
    )

    result = run_service.execute_run(
        _sess(session), run=run, checks=checks, runner=FakeRunner(outcome=outcome), table="T"
    )

    assert result.status == "succeeded"  # an errored check doesn't fail the run
    by_check = {r.check_id: r for r in session.added}
    errored = by_check[checks[0].id]
    assert errored.status == "error"  # not 'fail' — it never evaluated
    assert errored.metric_value is None
    assert errored.observed_value == {"error": outcome.checks[0].error_message}
    assert by_check[checks[1].id].status == "pass"  # sibling unaffected


def test_errored_sql_check_persists_no_statement_or_parameter_echo() -> None:
    """#1203: a SQL engine's error message must not carry target data into
    `observed_value`.

    GX hands `to_suite_outcome` `exception_info.exception_message`, which on a SQL
    execution engine is the rendering of a SQLAlchemy `StatementError` — the driver
    message followed by `[SQL: …]` and `[parameters: …]`. Those bound values are
    cells DataQ sent to the warehouse, and `observed_value` is outside both the
    `sample_failures` retention sweep and the suite's column policy, so it reaches
    the run-detail API, the UI, alerts and MCP output unmasked.

    Built from a REAL `StatementError` (not a hand-written string) so the test
    tracks SQLAlchemy's actual rendering rather than our idea of it. The same
    wrapper covers Snowflake and Unity Catalog — `_build_result` is the one choke
    point both reach, so neither can diverge."""
    statement_error = StatementError(
        "(snowflake.connector.errors.ProgrammingError) 100038 (22018): "
        "Numeric value 'ORD-9' is not recognized",
        "SELECT * FROM RETAIL.ORDERS WHERE CUSTOMER_REF = %(ref)s",
        {"ref": "alice@example.com"},
        Exception("orig"),
    )
    assert "alice@example.com" in str(statement_error)  # the premise, not an artefact

    session = FakeSession()
    run = _run()
    check = _checks(1)[0]
    outcome = SuiteOutcome(
        success=False,
        checks=[
            CheckOutcome(
                "unexpected_rows_expectation",
                success=False,
                errored=True,
                error_message=str(statement_error),
            )
        ],
    )

    run_service.execute_run(
        _sess(session), run=run, checks=[check], runner=FakeRunner(outcome=outcome), table="T"
    )

    persisted = session.added[0]
    assert persisted.status == "error"
    assert persisted.observed_value is not None
    stored = persisted.observed_value["error"]
    assert "alice@example.com" not in stored  # the bound parameter — target data
    assert "[SQL:" not in stored
    assert "[parameters:" not in stored
    assert "RETAIL.ORDERS" not in stored
    # The driver's own diagnostic survives — blanket-classifying it would make a bad
    # cast undiagnosable from the UI (the `SafeMonitorError` trade-off).
    assert stored == (
        "(snowflake.connector.errors.ProgrammingError) 100038 (22018): "
        "Numeric value 'ORD-9' is not recognized"
    )


def test_redact_observed_value_strips_the_echo_from_an_already_persisted_row() -> None:
    """#1203 read side: rows written before the fix still hold the echo, and
    `observed_value` has no retention sweep to age them out. `redact_observed_value`
    is what the run-detail API, the MCP tools and the alert builder all call, so
    stripping there corrects the whole history in one place."""
    legacy = {
        "error": (
            "(databricks.sql.exc.ServerOperationError) cannot cast\n"
            "[SQL: SELECT * FROM gold.feedback WHERE email = %(e)s]\n"
            "[parameters: {'e': 'bob@example.com'}]"
        )
    }

    out = run_service.redact_observed_value(legacy)

    assert out is not None
    assert out["error"] == "(databricks.sql.exc.ServerOperationError) cannot cast"
    assert "bob@example.com" not in out["error"]
    assert legacy["error"].count("[SQL:") == 1  # the caller's dict is not mutated


def test_redact_observed_value_strips_the_echo_alongside_an_unparsed_cell() -> None:
    """The two redactions compose: the #989 cell keeps its column-policy treatment
    while the #1203 echo goes, so neither fix can shadow the other."""
    out = run_service.redact_observed_value(
        {
            "error": "(x.Error) boom\n[SQL: SELECT 1]\n[parameters: {'p': 'leaked'}]",
            "unparsed_value": "not-a-timestamp",
            "column": "EMAIL",
        },
        policy={"pii_columns": ["EMAIL"]},
    )

    assert out is not None
    assert out["error"] == "(x.Error) boom"
    assert "leaked" not in out["error"]
    assert out["unparsed_value"] == "<redacted>"  # known-sensitive column, still masked


def test_errored_check_with_thresholds_is_still_error_not_banded() -> None:
    """Thresholds don't apply to an errored check — there's no metric to band, so
    it must resolve to `error`, not slip through severity derivation as a tier."""
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
    outcome = SuiteOutcome(success=False, checks=[CheckOutcome("x", success=False, errored=True)])

    run_service.execute_run(
        _sess(session), run=run, checks=[check], runner=FakeRunner(outcome=outcome), table="T"
    )

    persisted = session.added[0]
    assert persisted.status == "error"
    assert persisted.metric_value is None
    assert persisted.observed_value is None  # no message → no observed payload


def test_skipped_check_maps_to_skip_status_among_normal_siblings() -> None:
    """A per-check `skip` (#593 cold start) is the third operational outcome: it
    persists as `skip` with a NULL metric and its own explanatory payload, while
    its siblings still band normally and the RUN still succeeds.

    Until now `skip` only ever arrived run-wide via `skip_run`, so this pins that
    a mixed run persists cleanly (the status is a valid per-row value) rather than
    tripping the CHECK constraint or being coerced to pass/fail."""
    session = FakeSession()
    run = _run()
    checks = _checks(3)
    checks[1].fail_threshold = Decimal("3")
    outcome = SuiteOutcome(
        success=True,
        checks=[
            CheckOutcome("expect_ok", success=True),
            CheckOutcome(
                "monitor:anomaly",
                success=True,
                skipped=True,
                observed_value={"insufficient_history": True, "points": 2},
            ),
            CheckOutcome("expect_ok2", success=True),
        ],
    )

    result = run_service.execute_run(
        _sess(session), run=run, checks=checks, runner=FakeRunner(outcome=outcome), table="T"
    )

    assert result.status == "succeeded"
    by_check = {r.check_id: r for r in session.added}
    skipped = by_check[checks[1].id]
    assert skipped.status == "skip"
    # Never a fabricated z-score: a cold-start number would be trended and
    # baselined as if it were a measurement.
    assert skipped.metric_value is None
    assert skipped.observed_value == {"insufficient_history": True, "points": 2}
    assert by_check[checks[0].id].status == "pass"
    assert by_check[checks[2].id].status == "pass"


def test_skip_run_marks_all_checks_skip_and_run_succeeded() -> None:
    """skip_run (#122) records a `skip` Result per check without an adapter run,
    and the run succeeds — it executed, it just had nothing to validate."""
    session = FakeSession()
    run = _run()
    checks = _checks(3)

    result = run_service.skip_run(_sess(session), run=run, checks=checks, reason="batch_not_found")

    assert result.status == "succeeded"
    assert run.started_at is not None and run.finished_at is not None
    assert len(session.added) == 3
    assert all(r.status == "skip" for r in session.added)
    assert all(r.observed_value == {"reason": "batch_not_found"} for r in session.added)
    assert all(r.metric_value is None for r in session.added)


def test_cancel_during_execution_keeps_cancelled_and_persists_no_results() -> None:
    """If a cancel commits while GX is running, the worker must not overwrite it
    with a terminal success: refresh() sees the 'cancelled' status, the moot
    results are rolled back, and the run stays cancelled (A2 cooperative guard)."""
    session = FakeSession(refresh_status="cancelled")  # a concurrent cancel landed
    run = _run()
    checks = _checks(1)
    outcome = SuiteOutcome(success=True, checks=[CheckOutcome("x", success=True)])

    result = run_service.execute_run(
        _sess(session), run=run, checks=checks, runner=FakeRunner(outcome=outcome), table="T"
    )

    assert result.status == "cancelled"  # not 'succeeded' — cancel wins
    assert session.added == []  # staged results rolled back, nothing persisted
    assert session.rollbacks >= 1


def test_cancel_during_execution_that_also_errors_stays_cancelled() -> None:
    """A run cancelled mid-flight that then ALSO raises must stay 'cancelled', not
    be masked as 'failed' (the cooperative guard applies on the failure path too)."""
    session = FakeSession(refresh_status="cancelled")  # cancel landed during the run
    run = _run()
    runner = FakeRunner(raises=RuntimeError("warehouse dropped mid-run"))

    result = run_service.execute_run(
        _sess(session), run=run, checks=_checks(1), runner=runner, table="T"
    )

    assert result.status == "cancelled"  # not 'failed'
    assert session.added == []


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

    result = run_service.execute_run(
        _sess(session), run=run, checks=[freshness], runner=runner, table="T"
    )

    assert result.status == "failed"  # NotImplementedError → terminal 'failed'
    assert runner.called_with is None  # dispatch short-circuited before the adapter
    assert session.added == []  # nothing persisted
    assert session.rollbacks == 1


# ── redact_sample_failures (#226) ─────────────────────────────────────────────


def test_redact_sample_failures_none_and_empty_pass_through() -> None:
    assert run_service.redact_sample_failures(None) is None
    assert run_service.redact_sample_failures({}) is None


def test_redact_sample_failures_keeps_counts_classifies_row_dicts() -> None:
    # A dict-shaped list is redacted per column by the classifier: the `id` locator is
    # shown, the `ssn` PII masked (counts always kept).
    out = run_service.redact_sample_failures(
        {
            "unexpected_count": 3,
            "unexpected_percent": 12.5,
            "partial_unexpected_list": [{"id": 1, "ssn": "111-22-3333"}],
        }
    )
    assert out == {
        "unexpected_count": 3,
        "unexpected_percent": 12.5,
        "partial_unexpected_list": [{"id": 1, "ssn": "<redacted>"}],
    }


def test_redact_sample_failures_masks_scalar_list_preserving_length() -> None:
    # Column-values expectations yield a flat list of raw cell values, not dicts.
    out = run_service.redact_sample_failures(
        {"partial_unexpected_list": ["a@x.com", "b@y.com", "c@z.com"]}
    )
    assert out == {"partial_unexpected_list": ["<redacted>", "<redacted>", "<redacted>"]}


def test_redact_sample_failures_masks_unknown_keys_and_nested_values() -> None:
    # Any non-summary key is treated as data and fully masked, including nesting.
    out = run_service.redact_sample_failures(
        {"unexpected_index_list": [{"row": {"name": "Alice"}}]}
    )
    assert out == {"unexpected_index_list": [{"row": {"name": "<redacted>"}}]}


# ── the sample bound is re-applied at read time (#1196) ───────────────────────


def test_redact_bounds_oversized_lists_from_already_persisted_rows() -> None:
    """#1196: capture-time capping only protects NEW rows. Every result written
    before it — a pandas-backed check that failed thousands of rows under GX's
    uncapped `unexpected_index_list` — must stop shipping the whole list on every
    run-detail load, so the read path re-applies the same bound (the #1115
    read-time-derivation pattern: old rows corrected for free, nothing to backfill)."""
    rows = [{"ORDER_NUMBER": f"ORD-{i}", "LINE_TOTAL": -1.0 * i} for i in range(5_000)]
    out = run_service.redact_sample_failures(
        {
            "unexpected_index_list": rows,
            "partial_unexpected_list": [-1.0 * i for i in range(5_000)],
            "unexpected_count": 5_000,
        },
        tested_column="LINE_TOTAL",
        policy={"identifier_column": "ORDER_NUMBER"},
    )
    assert out is not None
    assert len(out["unexpected_index_list"]) == SAMPLE_ROW_CAP
    assert len(out["partial_unexpected_list"]) == SAMPLE_ROW_CAP
    # bounded, never falsified: the aggregate total still reports the real count,
    # and the retained rows are the first ones (unchanged apart from redaction).
    assert out["unexpected_count"] == 5_000
    assert out["unexpected_index_list"] == rows[:SAMPLE_ROW_CAP]


def test_read_time_bound_classifies_over_the_full_list_not_the_capped_slice() -> None:
    """#1196 review: bounding the payload must never widen what the payload reveals.

    `column_classification._value_signal` is *ratio*-based (emails >= 50% of the
    sampled values -> PII). A legacy oversized sample whose identifier-designated
    column holds emails in 80% of 5,000 rows but only 45% of the FIRST 20 flips from
    PII to shown the moment the classifier is fed the capped slice instead of the
    persisted list — unmasking real addresses that were masked before the cap
    existed. The emitted rows are capped; the classification input is not.
    """
    rows = [
        {
            # `_policy_identifier` designates this column, so it shows unless the
            # VALUE signal proves it sensitive — exactly the ratio the slice skews.
            # 9 of the first 20 rows are emails (0.45 — under the 0.5 threshold),
            # but nearly the whole 5,000-row list is (0.998 — well over it).
            "CUSTOMER_REF": (f"user{i}@example.com" if i >= 11 else f"REF-{i}"),
            "LINE_TOTAL": -1.0 * i,
        }
        for i in range(5_000)
    ]
    # sanity: the heuristic really does disagree between the window and the whole list
    first_window = (
        sum("@" in str(r["CUSTOMER_REF"]) for r in rows[:SAMPLE_ROW_CAP]) / SAMPLE_ROW_CAP
    )
    whole_list = sum("@" in str(r["CUSTOMER_REF"]) for r in rows) / len(rows)
    assert first_window < 0.5 <= whole_list

    out = run_service.redact_sample_failures(
        {"unexpected_index_list": rows, "unexpected_count": 5_000},
        tested_column="LINE_TOTAL",
        policy={"identifier_column": "CUSTOMER_REF"},
    )
    assert out is not None
    emitted = out["unexpected_index_list"]
    assert len(emitted) == SAMPLE_ROW_CAP  # still bounded
    assert all(row["CUSTOMER_REF"] == "<redacted>" for row in emitted)
    assert not any("@example.com" in str(row["CUSTOMER_REF"]) for row in emitted)


def test_read_time_bound_classifies_the_full_partial_list_too() -> None:
    """The same rule on the scalar `partial_unexpected_list` path: `_known_sensitive`
    judges the tested column over every persisted value, not the emitted window."""
    values = [f"user{i}@example.com" if i >= 11 else f"REF-{i}" for i in range(5_000)]
    out = run_service.redact_sample_failures(
        {"partial_unexpected_list": values, "unexpected_count": 5_000},
        tested_column="CUSTOMER_REF",
    )
    assert out is not None
    assert out["partial_unexpected_list"] == ["<redacted>"] * SAMPLE_ROW_CAP


# ── persisted value-signal summary restores the full-population ratio (#1230) ──


def test_redact_prefers_persisted_value_signal_summary_over_the_capped_window() -> None:
    """Since #1196, `unexpected_index_list` is ALREADY capped to `SAMPLE_ROW_CAP` at
    CAPTURE time for new rows — unlike the #1196-era legacy-row scenario above, there
    is no larger persisted list here to fall back to; the 20 rows in the DB are all
    there ever was. `gx_runner` now also persists a `value_signal_summary` alongside
    those capped rows (#1230), computed over the FULL pre-cap population, so the read
    path can still classify correctly.

    Only 6 of these 20 capped rows are emails (30%, under the classifier's 50%
    threshold) — judged on the capped window alone this column would misread as
    "not PII" and get shown. The persisted summary says the true population was 60%
    email, which must win.
    """
    capped_rows = [
        {"CUSTOMER_REF": (f"user{i}@x.com" if i < 6 else f"REF-{i}"), "QTY": -i}
        for i in range(SAMPLE_ROW_CAP)
    ]
    window_ratio = sum("@" in str(row["CUSTOMER_REF"]) for row in capped_rows) / SAMPLE_ROW_CAP
    assert window_ratio < 0.5  # sanity: the capped window alone reads "not PII"
    summary = {
        "CUSTOMER_REF": {
            "n": 5000,
            "email_count": 3000,  # 60% of the real, pre-cap population
            "id_shaped_count": 0,
            "encoded_count": 0,
            "distinct_count": 5000,
        }
    }
    out = run_service.redact_sample_failures(
        {
            "unexpected_index_list": capped_rows,
            "unexpected_count": 5000,
            "value_signal_summary": summary,
        },
        policy={"identifier_column": "CUSTOMER_REF"},
    )
    assert out is not None
    # internal capture-time metadata, consumed above but never re-emitted to a reader
    assert "value_signal_summary" not in out
    assert all(row["CUSTOMER_REF"] == "<redacted>" for row in out["unexpected_index_list"])


def test_redact_prefers_the_persisted_summary_for_the_scalar_partial_unexpected_list_too() -> None:
    """Review finding on #1230: the persisted `value_signal_summary` describes the
    COLUMN, not `unexpected_index_list`'s own contents — so it's equally valid
    evidence for the sibling scalar `partial_unexpected_list` (GX caps that list to
    ~20 on every engine; that fact only means the LIST'S OWN population can't grow
    the summary, not that the summary shouldn't be consulted when redacting it).

    Only 6 of these 20 values are emails (30%, under the 50% threshold) — judged on
    `partial_unexpected_list` alone this column reads "not PII" and would be shown.
    The persisted summary (from the sibling `unexpected_index_list`'s full
    pre-cap population) says the true population was 60% email, which must still
    win here, exactly as it does for `unexpected_index_list` itself.
    """
    scalar_values = [(f"user{i}@x.com" if i < 6 else f"REF-{i}") for i in range(SAMPLE_ROW_CAP)]
    window_ratio = sum("@" in v for v in scalar_values) / SAMPLE_ROW_CAP
    assert window_ratio < 0.5  # sanity: the capped window alone reads "not PII"
    summary = {
        "CUSTOMER_REF": {
            "n": 5000,
            "email_count": 3000,  # 60% of the real, pre-cap population
            "id_shaped_count": 0,
            "encoded_count": 0,
            "distinct_count": 5000,
        }
    }
    out = run_service.redact_sample_failures(
        {"partial_unexpected_list": scalar_values, "value_signal_summary": summary},
        tested_column="CUSTOMER_REF",
    )
    assert out is not None
    assert all(v == "<redacted>" for v in out["partial_unexpected_list"])


def test_redact_prefers_the_persisted_summary_for_dict_shaped_partial_unexpected_list_too() -> None:
    """Same gap, the dict-row branch of `partial_unexpected_list` (multicolumn-style
    expectations)."""
    dict_rows = [
        {"CUSTOMER_REF": (f"user{i}@x.com" if i < 6 else f"REF-{i}")} for i in range(SAMPLE_ROW_CAP)
    ]
    summary = {
        "CUSTOMER_REF": {
            "n": 5000,
            "email_count": 3000,
            "id_shaped_count": 0,
            "encoded_count": 0,
            "distinct_count": 5000,
        }
    }
    out = run_service.redact_sample_failures(
        {"partial_unexpected_list": dict_rows, "value_signal_summary": summary},
        tested_column="CUSTOMER_REF",
    )
    assert out is not None
    assert all(row["CUSTOMER_REF"] == "<redacted>" for row in out["partial_unexpected_list"])


def test_redact_falls_back_to_the_capped_window_when_no_summary_is_persisted() -> None:
    """Old rows — written before #1230, or from a non-pandas engine that never had a
    full population to summarise — carry no `value_signal_summary` key at all.
    Classification must fall back to the capped rows exactly as it did before this
    change, not error and not silently over-mask."""
    capped_rows = [{"CUSTOMER_REF": f"REF-{i}", "QTY": -i} for i in range(SAMPLE_ROW_CAP)]
    out = run_service.redact_sample_failures(
        {"unexpected_index_list": capped_rows, "unexpected_count": SAMPLE_ROW_CAP},
        policy={"identifier_column": "CUSTOMER_REF"},
    )
    assert out is not None
    assert "value_signal_summary" not in out
    assert all(row["CUSTOMER_REF"] != "<redacted>" for row in out["unexpected_index_list"])


def test_redact_ignores_a_malformed_persisted_summary() -> None:
    """A corrupt/hand-edited `value_signal_summary` (missing keys, wrong types) must
    not crash the read path or be trusted as evidence — it falls back to the capped
    rows exactly like the no-summary case, per `column_classification._valid_summary`."""
    capped_rows = [{"CUSTOMER_REF": f"REF-{i}", "QTY": -i} for i in range(SAMPLE_ROW_CAP)]
    out = run_service.redact_sample_failures(
        {
            "unexpected_index_list": capped_rows,
            "unexpected_count": SAMPLE_ROW_CAP,
            "value_signal_summary": {"CUSTOMER_REF": {"n": 0}},  # malformed: missing keys, n<=0
        },
        policy={"identifier_column": "CUSTOMER_REF"},
    )
    assert out is not None
    assert all(row["CUSTOMER_REF"] != "<redacted>" for row in out["unexpected_index_list"])


# ── column-aware redaction (#415) ─────────────────────────────────────────────


def test_redact_surfaces_non_pii_tested_column_values() -> None:
    # The whole point: a non-PII tested column's failing values are now shown.
    out = run_service.redact_sample_failures(
        {"unexpected_count": 2, "partial_unexpected_list": [-12.5, -5.0]},
        tested_column="LINE_TOTAL",
    )
    assert out == {"unexpected_count": 2, "partial_unexpected_list": [-12.5, -5.0]}


def test_redact_masks_pii_tested_column_by_name_heuristic() -> None:
    # A PII-looking tested column stays masked even when it's the tested column.
    out = run_service.redact_sample_failures(
        {"partial_unexpected_list": ["a@x.com", "b@y.com"]},
        tested_column="CUSTOMER_EMAIL",
    )
    assert out == {"partial_unexpected_list": ["<redacted>", "<redacted>"]}


def test_redact_masks_tested_column_listed_in_policy_pii() -> None:
    # An explicit policy PII list masks a column the heuristic wouldn't catch.
    out = run_service.redact_sample_failures(
        {"partial_unexpected_list": [42, 43]},
        tested_column="SALARY",
        policy={"pii_columns": ["SALARY"]},
    )
    assert out == {"partial_unexpected_list": ["<redacted>", "<redacted>"]}


def test_redact_index_list_shows_identifier_and_tested_masks_rest() -> None:
    # Row-dicts: identifier + tested column shown, PII + unclassified masked.
    out = run_service.redact_sample_failures(
        {
            "unexpected_index_list": [
                {"ORDER_NUMBER": "ORD-1041", "LINE_TOTAL": -12.5, "EMAIL": "a@x.com"},
            ]
        },
        tested_column="LINE_TOTAL",
        policy={"identifier_column": "ORDER_NUMBER"},
    )
    assert out == {
        "unexpected_index_list": [
            {"ORDER_NUMBER": "ORD-1041", "LINE_TOTAL": -12.5, "EMAIL": "<redacted>"},
        ]
    }


def test_redact_sample_failures_masks_non_numeric_value_under_safe_key() -> None:
    # The safe-key passthrough trusts value *shape*: a non-number under a safe key
    # (a hypothetical future runner stowing row data there) must still be masked.
    out = run_service.redact_sample_failures(
        {
            "unexpected_count": 3,  # genuine scalar → kept
            "unexpected_percent": ["secret@x.com"],  # not a number → masked
        }
    )
    assert out == {"unexpected_count": 3, "unexpected_percent": ["<redacted>"]}


def test_redact_shows_surrogate_person_key_by_classifier() -> None:
    # No policy: the classifier alone shows a surrogate key (customer_id) as the row
    # locator and masks the PII, without any explicit identifier_column.
    out = run_service.redact_sample_failures(
        {
            "unexpected_index_list": [
                {"CUSTOMER_ID": 4471, "QTY": -3, "CUSTOMER_EMAIL": "a@x.com"},
            ]
        },
        tested_column="QTY",
    )
    assert out == {
        "unexpected_index_list": [{"CUSTOMER_ID": 4471, "QTY": -3, "CUSTOMER_EMAIL": "<redacted>"}]
    }


def test_redact_masks_natural_key_holding_emails() -> None:
    # A `user_id` whose VALUES are emails is a natural key leaking a direct identifier —
    # the value signal overrides the id-shaped name and masks it.
    out = run_service.redact_sample_failures(
        {
            "unexpected_index_list": [
                {"USER_ID": "ada@acme.io", "STATUS": "bad"},
                {"USER_ID": "bo@acme.io", "STATUS": "bad"},
            ]
        },
    )
    assert out == {
        "unexpected_index_list": [
            {"USER_ID": "<redacted>", "STATUS": "bad"},
            {"USER_ID": "<redacted>", "STATUS": "bad"},
        ]
    }


def test_redact_identifier_override_cannot_unmask_pii_column() -> None:
    # A designated identifier that is affirmatively PII (name) must stay masked — an
    # override picks a locator, it can't un-mask a direct identifier.
    out = run_service.redact_sample_failures(
        {"unexpected_index_list": [{"EMAIL": "a@x.com", "QTY": -3}]},
        tested_column="QTY",
        policy={"identifier_column": "EMAIL"},
    )
    assert out == {"unexpected_index_list": [{"EMAIL": "<redacted>", "QTY": -3}]}


def test_redact_identifier_override_masks_natural_key_of_emails() -> None:
    # A `user_id` designated identifier whose VALUES are emails → value floor masks it.
    out = run_service.redact_sample_failures(
        {"unexpected_index_list": [{"USER_ID": "a@x.com", "QTY": -3}]},
        tested_column="QTY",
        policy={"identifier_column": "USER_ID"},
    )
    assert out == {"unexpected_index_list": [{"USER_ID": "<redacted>", "QTY": -3}]}


def test_redact_tested_column_match_is_case_insensitive() -> None:
    # GX returns warehouse casing (Snowflake upper-cases); the check config's column may
    # differ in case. The tested column's non-PII value must still surface.
    out = run_service.redact_sample_failures(
        {"unexpected_index_list": [{"LINE_TOTAL": -12.5, "EMAIL": "a@x.com"}]},
        tested_column="line_total",
    )
    assert out == {"unexpected_index_list": [{"LINE_TOTAL": -12.5, "EMAIL": "<redacted>"}]}


def test_redact_datasource_tag_is_a_floor_override_cannot_unmask() -> None:
    # Level 1 governance tag marks a column sensitive; even an explicit identifier_column
    # override (level 3) cannot un-mask it.
    out = run_service.redact_sample_failures(
        {"unexpected_index_list": [{"ACCOUNT_REF": "ACC-9", "AMOUNT": -1}]},
        tested_column="AMOUNT",
        policy={"identifier_column": "ACCOUNT_REF"},
        tags={"ACCOUNT_REF": "sensitive"},
    )
    assert out == {"unexpected_index_list": [{"ACCOUNT_REF": "<redacted>", "AMOUNT": -1}]}


# ── redact_sample_failures_with_state (#424) ──────────────────────────────────
# The header lied whenever ANY value surfaced after #415's column-aware masking:
# it always said "values redacted". These test the tri-state summary the read
# API now derives instead of the frontend sniffing for the "<redacted>" sentinel
# (which breaks if a genuine value equals it).


def test_redact_state_none_for_a_none_sample() -> None:
    sample, state, cols = run_service.redact_sample_failures_with_state(None)
    assert sample is None
    assert state is None
    assert cols == []


def test_redact_state_null_when_sample_has_no_data_bearing_content() -> None:
    # Only aggregate counts — nothing was ever shown or masked, so there is
    # nothing true to claim either way.
    sample, state, cols = run_service.redact_sample_failures_with_state(
        {"unexpected_count": 3, "unexpected_percent": 12.5}
    )
    assert sample == {"unexpected_count": 3, "unexpected_percent": 12.5}
    assert state is None
    assert cols == []


def test_redact_state_full_when_every_column_masked() -> None:
    sample, state, cols = run_service.redact_sample_failures_with_state(
        {"unexpected_index_list": [{"EMAIL": "a@x.com", "SSN": "111-22-3333"}]},
    )
    assert state == "full"
    assert cols == ["EMAIL", "SSN"]
    assert sample == {"unexpected_index_list": [{"EMAIL": "<redacted>", "SSN": "<redacted>"}]}


def test_redact_state_full_for_anonymous_masked_scalar_list_with_no_tested_column() -> None:
    # No tested_column context → the scalar list masks with no column name to
    # attribute the decision to. The mask must still register as "full" — the
    # anonymous-masked path must not silently disappear from the summary.
    sample, state, cols = run_service.redact_sample_failures_with_state(
        {"partial_unexpected_list": ["a@x.com", "b@y.com"]}
    )
    assert state == "full"
    assert cols == []  # nothing nameable, but the mask is still counted
    assert sample == {"partial_unexpected_list": ["<redacted>", "<redacted>"]}


def test_redact_state_partial_with_no_nameable_column_from_anonymous_mask() -> None:
    """#1115 review: an anonymous mask (no nameable column) can coincide with a
    DIFFERENT column being shown in the SAME rendered list. That combination reports
    "partial" with an EMPTY `redacted_columns` (there is a real mask, but nothing
    nameable for it) — the API/frontend must not read empty `redacted_columns` as
    "nothing was masked" when state is "partial".

    It also pins #1197's guard. This sample renders nothing (a mixed dict/non-dict
    `unexpected_index_list` fails the frontend's `isIdentifierRows`, and there is no
    `partial_unexpected_list` to fall back to), so `_displayed_sample_key` returns
    None and the displayed-list narrowing deliberately does NOT apply: with no winner
    there is no loser to suppress, and #1115's union semantics stand unchanged. Drop
    the `displayed_key is not None` guard and this reports None instead of "partial".

    Before #1197 the property was demonstrated with the mask coming from a *different*
    list (`partial_unexpected_list`) — which is exactly the cross-list accumulation
    #1197 removed, since that list is not on screen when the index list is."""
    sample, state, cols = run_service.redact_sample_failures_with_state(
        {
            "unexpected_index_list": [
                {"ORDER_ID": "ORD-1"},  # shown: identifier
                "not-a-row-dict",  # masked, nothing nameable
            ],
        }
    )
    assert state == "partial"
    assert cols == []  # nothing nameable for the anonymous mask
    assert sample == {"unexpected_index_list": [{"ORDER_ID": "ORD-1"}, "<redacted>"]}


# ── the label describes the DISPLAYED list, not the union of both (#1197) ─────


def test_redact_state_ignores_the_list_the_frontend_does_not_render() -> None:
    """#1197: `unexpected_index_list` and `partial_unexpected_list` are two
    renderings of the same failing rows, and the run-detail table shows exactly one —
    the dict-shaped index list when present (#1190). Masking that happens only in the
    list nobody sees must not appear in the label for the table they do see.

    Displayed: one row whose `ORDER_ID` surfaces as an identifier — everything on
    screen is shown, so the honest claim is "values shown". The scalar
    `partial_unexpected_list` beside it masks (no `tested_column` to authorise it),
    which used to drag the label to "partial" over a table with nothing redacted in
    it."""
    sample, state, cols = run_service.redact_sample_failures_with_state(
        {
            "unexpected_index_list": [{"ORDER_ID": "ORD-1"}],  # displayed, all shown
            "partial_unexpected_list": ["a@x.com"],  # not displayed; masks
        }
    )
    assert state == "none"
    assert cols == []
    # the list that is NOT rendered is still redacted on its own terms — narrowing
    # the tracker must never narrow the masking.
    assert sample == {
        "unexpected_index_list": [{"ORDER_ID": "ORD-1"}],
        "partial_unexpected_list": ["<redacted>"],
    }


def test_redact_state_masked_column_stays_named_when_the_other_list_would_show_it() -> None:
    """The exact undercount #1197 describes: a column that masks in the DISPLAYED
    index list but classifies as shown from the other list's own sample must still
    appear in `redacted_columns`. `tested_column` names it in both, so the old
    cross-list OR flipped it to "shown" and reported "values shown" over a table
    whose every cell for that column read "<redacted>"."""
    sample, state, cols = run_service.redact_sample_failures_with_state(
        {
            # In the index list the column's own values are emails → PII → masked.
            "unexpected_index_list": [{"CUSTOMER_REF": "a@x.com"}, {"CUSTOMER_REF": "b@x.com"}],
            # In the scalar list the same column's sample is innocuous → shown.
            "partial_unexpected_list": ["REF-1", "REF-2"],
        },
        tested_column="CUSTOMER_REF",
    )
    assert state == "full"
    assert cols == ["CUSTOMER_REF"]
    assert sample is not None
    assert sample["unexpected_index_list"] == [
        {"CUSTOMER_REF": "<redacted>"},
        {"CUSTOMER_REF": "<redacted>"},
    ]


def test_redact_state_falls_back_to_the_partial_list_when_no_index_rows_render() -> None:
    """Mirror of the frontend fallback: a non-dict `unexpected_index_list` renders
    nothing, so `partial_unexpected_list` is the displayed list and sets the label."""
    sample, state, cols = run_service.redact_sample_failures_with_state(
        {
            "unexpected_index_list": [1, 4, 7],  # bare positional indices — not rows
            "partial_unexpected_list": [-12.5, -5.0],
        },
        tested_column="LINE_TOTAL",
    )
    assert state == "none"  # the displayed list's values are shown
    assert cols == []
    assert sample is not None
    assert sample["partial_unexpected_list"] == [-12.5, -5.0]
    assert sample["unexpected_index_list"] == ["<redacted>"] * 3


def test_displayed_list_is_decided_on_the_capped_rows_the_frontend_receives() -> None:
    """#1238 review: the winner must be picked from the same rows the UI tests.

    The read path re-applies `SAMPLE_ROW_CAP` (#1196), so a payload whose first
    `SAMPLE_ROW_CAP` `unexpected_index_list` entries are dicts but which carries a
    non-dict beyond the cap ships an ALL-dict list to the frontend — which therefore
    renders it. Deciding on the uncapped list would call `partial_unexpected_list`
    the displayed one and suppress the tracker on the table actually on screen: this
    fix's own bug, inverted.

    Here the displayed index list masks `CUSTOMER_REF` (its values are emails) while
    the scalar list would show it, so the two answers are distinguishable."""
    rows: list[Any] = [{"CUSTOMER_REF": f"a{i}@x.com"} for i in range(SAMPLE_ROW_CAP)]
    rows.append("not-a-row")  # beyond the cap — never reaches the frontend
    sample, state, cols = run_service.redact_sample_failures_with_state(
        {
            "unexpected_index_list": rows,
            "partial_unexpected_list": ["REF-1", "REF-2"],
        },
        tested_column="CUSTOMER_REF",
    )
    assert sample is not None
    # The emitted list is all-dict, i.e. exactly what the frontend renders.
    assert len(sample["unexpected_index_list"]) == SAMPLE_ROW_CAP
    assert all(isinstance(row, dict) for row in sample["unexpected_index_list"])
    # …so the label describes it, not the scalar fallback (which would give "none").
    assert state == "full"
    assert cols == ["CUSTOMER_REF"]


def test_redact_state_none_when_every_column_shown() -> None:
    # The tested column's non-PII values surface (#417) and nothing else is masked.
    sample, state, cols = run_service.redact_sample_failures_with_state(
        {"unexpected_count": 2, "partial_unexpected_list": [-12.5, -5.0]},
        tested_column="LINE_TOTAL",
    )
    assert state == "none"
    assert cols == []
    assert sample == {"unexpected_count": 2, "partial_unexpected_list": [-12.5, -5.0]}


def test_redact_state_partial_when_some_columns_shown_some_masked() -> None:
    sample, state, cols = run_service.redact_sample_failures_with_state(
        {
            "unexpected_index_list": [
                {"ORDER_NUMBER": "ORD-1041", "LINE_TOTAL": -12.5, "EMAIL": "a@x.com"},
            ]
        },
        tested_column="LINE_TOTAL",
        policy={"identifier_column": "ORDER_NUMBER"},
    )
    assert state == "partial"
    assert cols == ["EMAIL"]
    assert sample == {
        "unexpected_index_list": [
            {"ORDER_NUMBER": "ORD-1041", "LINE_TOTAL": -12.5, "EMAIL": "<redacted>"},
        ]
    }


def test_redact_state_partial_across_comparison_buckets() -> None:
    # Comparison rows (ADR 0015): an explicit `pii_columns` entry masks both sides
    # of STATUS while the unsuffixed, non-PII ORDER_ID join key surfaces.
    sample, state, cols = run_service.redact_sample_failures_with_state(
        {
            "mismatched": [
                {"ORDER_ID": "ORD-1", "STATUS_src": "shipped", "STATUS_tgt": "returned"},
            ]
        },
        policy={"pii_columns": ["STATUS"]},
    )
    assert state == "partial"
    assert cols == ["STATUS_src", "STATUS_tgt"]
    assert sample == {
        "mismatched": [
            {"ORDER_ID": "ORD-1", "STATUS_src": "<redacted>", "STATUS_tgt": "<redacted>"}
        ]
    }


def test_redact_state_a_column_shown_anywhere_counts_as_shown() -> None:
    # A column masked in one bucket but shown in another (rare, but the buckets are
    # independent) must not report as "redacted" — OR-of-shown matches what a
    # viewer actually saw somewhere in the sample.
    tracker = run_service._RedactionTracker()
    tracker.record("QTY", shown=False)
    tracker.record("QTY", shown=True)
    state, cols = tracker.summary()
    assert state == "none"
    assert cols == []


# ── #1115 review: the catch-all masked branches must also register with the
# tracker, or `summary()` UNDER-claims redaction — the mirror image of the bug
# #424 fixed. Unreachable with today's two writers (gx_runner / comparison_run
# only ever emit the recognized keys/shapes), but these are public helpers, so
# an unrecognized key or a malformed row shape must not silently vanish.


def test_redact_state_full_for_an_unrecognized_key_with_no_column_context() -> None:
    # A key the redactor doesn't recognize is masked (default-mask, #415) but has
    # no column name to attribute the mask to — must still register as a real
    # (anonymous) mask, not "nothing happened".
    sample, state, cols = run_service.redact_sample_failures_with_state(
        {"some_future_bucket": ["secret@x.com"]}
    )
    assert state == "full"
    assert cols == []
    assert sample == {"some_future_bucket": ["<redacted>"]}


def test_redact_state_partial_when_unrecognized_key_masks_alongside_a_shown_column() -> None:
    sample, state, cols = run_service.redact_sample_failures_with_state(
        {
            "unexpected_index_list": [{"ORDER_ID": "ORD-1"}],  # shown: identifier
            "some_future_bucket": ["secret@x.com"],  # masked, unrecognized key
        }
    )
    assert state == "partial"
    assert cols == []  # nothing nameable for the unrecognized-key mask
    assert sample == {
        "unexpected_index_list": [{"ORDER_ID": "ORD-1"}],
        "some_future_bucket": ["<redacted>"],
    }


def test_redact_state_full_for_a_malformed_non_dict_row_in_index_list() -> None:
    # `unexpected_index_list` is documented as row-dicts; a malformed non-dict
    # entry still masks (via `_redact_row`'s non-dict fallback) but has no column
    # identity — must still register as an anonymous mask.
    sample, state, cols = run_service.redact_sample_failures_with_state(
        {"unexpected_index_list": ["not-a-row-dict"]}
    )
    assert state == "full"
    assert cols == []
    assert sample == {"unexpected_index_list": ["<redacted>"]}


def test_redact_state_partial_for_a_malformed_non_dict_comparison_row() -> None:
    sample, state, cols = run_service.redact_sample_failures_with_state(
        {
            "mismatched": [
                {"ORDER_ID": "ORD-1"},  # shown: unsuffixed, non-PII join key
                "not-a-row-dict",  # malformed — masks anonymously
            ]
        }
    )
    assert state == "partial"
    assert cols == []
    assert sample == {"mismatched": [{"ORDER_ID": "ORD-1"}, "<redacted>"]}


# ── #989: the errored-monitor cell, redacted under the same policy ───────────


def test_redact_observed_value_passes_through_when_there_is_no_cell() -> None:
    # A plain errored result (message only) and a non-errored one must be untouched.
    assert run_service.redact_observed_value(None) is None
    assert run_service.redact_observed_value({"error": "boom"}) == {"error": "boom"}


def test_redact_observed_value_shows_a_non_sensitive_cell() -> None:
    """The diagnostic is the whole point: "your timestamp column has junk in it" is
    unactionable without the junk. A freshness column is the analogue of the tested
    column in a failing sample, so it shows unless *known* sensitive."""
    out = run_service.redact_observed_value(
        {
            "error": "…not a parseable timestamp",
            "unparsed_value": "13/07/2026",
            "column": "order_ts",
        }
    )
    assert out is not None and out["unparsed_value"] == "13/07/2026"


def test_redact_observed_value_masks_a_policy_pii_cell() -> None:
    """The case #989 exists for. Before this, a freshness monitor pointed at a
    column the user had declared PII echoed that column's value into
    `results.observed_value` and out to the UI, alerts and MCP — bypassing the
    redaction machinery that exists for exactly this."""
    out = run_service.redact_observed_value(
        {"unparsed_value": "not-a-date", "column": "email"},
        policy={"pii_columns": ["email"]},
    )
    assert out is not None and out["unparsed_value"] != "not-a-date"


def test_redact_observed_value_masks_on_a_governance_tag_too() -> None:
    """Tags are the floor authority — a datasource-tagged column masks even with no
    suite policy set, matching `redact_sample_failures`."""
    out = run_service.redact_observed_value(
        {"unparsed_value": "not-a-date", "column": "ssn"},
        tags={"ssn": "pii"},
    )
    assert out is not None and out["unparsed_value"] != "not-a-date"


def test_redact_observed_value_masks_when_the_column_is_unknown() -> None:
    """No column name means no way to consult the policy, so there is no basis to
    show the cell. Fail closed — the same default the sample path takes when it has
    no tested-column context."""
    out = run_service.redact_observed_value({"unparsed_value": "not-a-date", "column": None})
    assert out is not None and out["unparsed_value"] != "not-a-date"


# ── #1229: a set-oriented expectation's `observed_value` list ────────────────


def test_redact_observed_value_scalar_is_never_touched() -> None:
    # The aggregate/metric case (row counts, means) must pass through untouched —
    # only list/set-shaped observed_value is in scope for #1229.
    out = run_service.redact_observed_value({"observed_value": 74}, tested_column="LINE_TOTAL")
    assert out == {"observed_value": 74}


def test_redact_observed_value_surfaces_a_non_pii_tested_columns_set() -> None:
    # The whole point, same authority as `partial_unexpected_list`'s tested column.
    out = run_service.redact_observed_value(
        {"observed_value": [-12.5, -5.0]},
        tested_column="LINE_TOTAL",
    )
    assert out == {"observed_value": [-12.5, -5.0]}


def test_redact_observed_value_masks_a_pii_tested_columns_set_by_name_heuristic() -> None:
    # This is the exact exposure #1229 exists to close: expect_column_distinct_
    # values_to_be_in_set on a high-cardinality email column persisted every
    # distinct value it ever saw, unredacted.
    out = run_service.redact_observed_value(
        {"observed_value": ["a@x.com", "b@y.com"]},
        tested_column="CUSTOMER_EMAIL",
    )
    assert out == {"observed_value": ["<redacted>", "<redacted>"]}


def test_redact_observed_value_masks_a_policy_pii_tested_columns_set() -> None:
    out = run_service.redact_observed_value(
        {"observed_value": [42, 43]},
        tested_column="SALARY",
        policy={"pii_columns": ["SALARY"]},
    )
    assert out == {"observed_value": ["<redacted>", "<redacted>"]}


def test_redact_observed_value_masks_a_set_with_no_tested_column() -> None:
    # No tested_column context → no basis to show it. Fail closed, same default
    # `partial_unexpected_list` takes with no tested column.
    out = run_service.redact_observed_value({"observed_value": ["a", "b"]})
    assert out == {"observed_value": ["<redacted>", "<redacted>"]}


def test_redact_observed_value_re_caps_a_legacy_uncapped_set_at_read_time() -> None:
    # A result persisted BEFORE the capture-time cap (#1229) existed must not keep
    # shipping an unbounded payload on every read — same read-time re-cap reasoning
    # `redact_sample_failures` already applies (#1196).
    values = [f"cust-{i}" for i in range(5_000)]
    out = run_service.redact_observed_value(
        {"observed_value": values},
        tested_column="LINE_TOTAL",
    )
    assert out is not None
    assert len(out["observed_value"]) == SAMPLE_ROW_CAP
    assert out["observed_value"] == values[:SAMPLE_ROW_CAP]


def test_redact_observed_value_classifies_over_the_full_list_not_just_the_cap() -> None:
    # Bounding what is emitted must never widen what is examined: if the PII
    # signal only shows up after the first SAMPLE_ROW_CAP entries, the column must
    # still mask — judging a legacy oversized sample on only its first 20 rows
    # could otherwise flip a column from PII to shown.
    safe_prefix = [f"id-{i}" for i in range(SAMPLE_ROW_CAP)]
    pii_tail = [f"user{i}@example.com" for i in range(500)]
    out = run_service.redact_observed_value(
        {"observed_value": safe_prefix + pii_tail},
        tested_column="CUSTOMER_REF",
    )
    assert out is not None
    assert out["observed_value"] == ["<redacted>"] * SAMPLE_ROW_CAP


# ── sampled-ness on the result row + the guardrail's reason (#595) ───────────


def test_a_sampled_outcome_is_persisted_on_the_result_row() -> None:
    """The acceptance criterion's storage half: a check that passed on a sample
    must SAY so on the row a reader can query, not only in a worker log line."""
    session = FakeSession()
    run = _run()
    checks = _checks(1)
    record = {
        "strategy": "head",
        "requested_rows": 100,
        "rows": 100,
        "total_rows": None,
        "sampled": True,
    }
    runner = FakeRunner(
        SuiteOutcome(
            success=True,
            checks=[CheckOutcome(expectation_type="x", success=True, sampling=record)],
        )
    )

    run_service.execute_run(_sess(session), run=run, checks=checks, runner=runner, table="T")

    assert session.added[0].sampling == record


def test_an_unsampled_outcome_leaves_the_column_null() -> None:
    """`None`, not a `sampled: false` record — so "complete read" is one shape for
    a suite that never opted in and for every row written before the column
    existed, and a reader can branch on presence alone."""
    session = FakeSession()
    runner = FakeRunner(
        SuiteOutcome(success=True, checks=[CheckOutcome(expectation_type="x", success=True)])
    )

    run_service.execute_run(_sess(session), run=_run(), checks=_checks(1), runner=runner, table="T")

    assert session.added[0].sampling is None


def test_an_errored_check_still_records_that_the_read_was_sampled() -> None:
    """The errored branch drops `observed_value` and the sample because neither
    exists for a check that never evaluated — but "this run was reading a sample"
    is still true of the read that failed, and is often the explanation."""
    session = FakeSession()
    record = {
        "strategy": "random",
        "requested_rows": 10,
        "rows": 10,
        "total_rows": 99,
        "sampled": True,
    }
    runner = FakeRunner(
        SuiteOutcome(
            success=False,
            checks=[
                CheckOutcome(
                    expectation_type="x",
                    success=False,
                    errored=True,
                    error_message="column not found",
                    sampling=record,
                )
            ],
        )
    )

    run_service.execute_run(_sess(session), run=_run(), checks=_checks(1), runner=runner, table="T")

    assert session.added[0].status == "error"
    assert session.added[0].sampling == record


def test_a_scan_refusal_surfaces_its_own_message_not_the_generic_classification() -> None:
    """`classify_failure_reason` exists because a driver exception can echo a DSN
    or a cell value (#605). `ScanTooLargeError` is DataQ's own sentence, built
    from the user's run target and two settings integers — classifying it would
    replace the single most actionable message in the feature ("this file is over
    the cap, set a sampling strategy") with "see the server logs", which is
    exactly the undiagnosable outcome #755 already produces."""
    session = FakeSession()
    run = _run()
    runner = FakeRunner(
        raises=ScanTooLargeError(
            "file 'raw/huge.csv' is 999,000,000 bytes, over the scan cap of 268,435,456. "
            "Set a sampling strategy, or raise RUN_MAX_SCAN_BYTES deliberately."
        )
    )

    run_service.execute_run(_sess(session), run=run, checks=_checks(1), runner=runner, table="T")

    assert run.status == "failed"
    assert run.failure_reason is not None
    assert "RUN_MAX_SCAN_BYTES" in run.failure_reason
    assert "999,000,000" in run.failure_reason


def test_every_other_exception_is_still_classified() -> None:
    """The narrow `isinstance` must stay narrow: a driver error keeps its fixed,
    secret-free message. Pinned beside the exemption so widening the redaction
    contract cannot happen silently."""
    session = FakeSession()
    run = _run()
    runner = FakeRunner(raises=RuntimeError("login failed for user 'svc' at acct.example"))

    run_service.execute_run(_sess(session), run=run, checks=_checks(1), runner=runner, table="T")

    assert run.failure_reason is not None
    assert "svc" not in run.failure_reason
    assert "acct.example" not in run.failure_reason


# ───────────────────────── elapsed heartbeat (#318) ──────────────────


def test_elapsed_ms_clamps_a_backwards_clock_to_zero() -> None:
    """`started_at` is written by the WORKER and `now()` is read in the API
    process, so a small clock difference between the two can put the start in the
    future. That must read as "0 ms so far", never as a negative age the UI would
    render as `-00:03` — the display honesty rule, applied to a clock."""
    run = _run()
    run.started_at = datetime.now(UTC) + timedelta(seconds=5)
    run.finished_at = None

    assert run_service._elapsed_ms(run) == 0


def test_elapsed_ms_reads_a_naive_timestamp_as_utc_instead_of_raising() -> None:
    """Everything DataQ writes is tz-aware, but a read model must not 500 on a row
    that isn't (a hand-inserted row, a restore from a naive dump)."""
    run = _run()
    run.started_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=30)
    run.finished_at = None

    elapsed = run_service._elapsed_ms(run)
    assert elapsed is not None and 30_000 <= elapsed < 60_000
