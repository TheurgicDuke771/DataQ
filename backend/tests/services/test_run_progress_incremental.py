"""Per-phase incremental run progress (#318) — against real, COMMITTED Postgres rows.

`execute_run` commits each **publishable** execution phase rather than the whole
run at once, so a check that has genuinely resolved shows up in
`get_run_progress` while the run is still going. These tests pin the four things
that makes true, and the one thing it must not change:

1. **The increments are real.** A `comparison` check is its own publishable
   phase, so a poll *on another connection* — which is what the API process is —
   sees earlier checks resolved mid-run.
2. **The granularity is honest.** Expectations are one phase no matter how many
   there are (GX validates them atomically), so a poll genuinely stays at 0
   through them. The test asserts that rather than pretending otherwise.
3. **Durability outranks progress.** The stateful monitor kinds write
   `monitor_baselines` through the run's own session, so their rows are NOT
   published early — committing them would make a failed run's baseline
   permanent. They run last and land with the terminal commit.
4. **The terminal contract is unchanged.** A run that ends `failed` or
   `cancelled` still has **no** result rows, even though earlier phases had
   already committed some — `discard_run_results` deletes them.

Deliberately NOT the `db_session` fixture (the `test_poll_lock_timeout` lesson):
that fixture wraps each test in an outer transaction it rolls back, with
`join_transaction_mode="create_savepoint"`, so everything `execute_run` "commits"
lands on a savepoint no other connection can ever see. A test of cross-transaction
visibility run on that fixture proves nothing — it would pass identically against
the old commit-once-at-the-end code. So these build their fixtures on the raw
`_db_engine` and clean up explicitly.

Skips without `TEST_DATABASE_URL` (via `_db_engine`).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from sqlalchemy import delete, event, select
from sqlalchemy.orm import Session as SASession

from backend.app.datasources.base import CheckOutcome, CheckSpec, SuiteOutcome
from backend.app.db.models import COMPARISON_KIND, Check, Connection, Result, Run, Suite, User
from backend.app.services import run_service


class _Runner:
    """A GX-shaped runner: one atomic `run_checks` for the whole expectation batch."""

    supported_monitor_kinds: frozenset[str] = frozenset()

    def __init__(
        self, *, raises: Exception | None = None, before: Callable[[], None] | None = None
    ) -> None:
        self._raises = raises
        self._before = before
        self.calls = 0

    def run_checks(
        self,
        *,
        table: str,
        schema: str | None,
        checks: list[CheckSpec],
        index_columns: list[str] | None = None,
    ) -> SuiteOutcome:
        self.calls += 1
        if self._before is not None:
            self._before()
        if self._raises is not None:
            raise self._raises
        outcomes = [CheckOutcome(expectation_type=c.expectation_type, success=True) for c in checks]
        return SuiteOutcome(success=True, checks=outcomes)


def _ok(check: Check) -> CheckOutcome:
    return CheckOutcome(expectation_type=check.expectation_type, success=True)


class _Fixture:
    """A suite + checks committed for real, plus the tools to poll and clean up."""

    def __init__(self, engine: Any, kinds: list[str]) -> None:
        self.engine = engine
        self.session = SASession(bind=engine)
        owner = User(aad_object_id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex[:10]}@ex.io")
        self.session.add(owner)
        self.session.flush()
        conn = Connection(
            name=f"c-{uuid.uuid4().hex[:8]}",
            type="snowflake",
            env="dev",
            config={"account": "a", "warehouse": "w", "database": "d", "role": "r"},
            secret_ref="kv-ref",
            created_by=owner.id,
        )
        self.session.add(conn)
        self.session.flush()
        suite = Suite(
            name=f"s-{uuid.uuid4().hex[:8]}",
            connection_id=conn.id,
            created_by=owner.id,
            target={"table": "T"},
        )
        self.session.add(suite)
        self.session.commit()
        self.owner_id, self.connection_id, self.suite_id = owner.id, conn.id, suite.id
        self.suite = suite
        self.checks: list[Check] = []
        for i, kind in enumerate(kinds):
            check = Check(
                suite_id=suite.id,
                name=f"c{i}",
                expectation_type="expect_x",
                kind=kind,
                config={},
                # `ck_checks_comparison_source_presence` requires a comparison
                # check to name the connection its SOURCE side reads from (ADR
                # 0015). Pointing it at the suite's own connection is enough for
                # the run path here — the executor is injected, so nothing
                # actually connects.
                source_connection_id=conn.id if kind == COMPARISON_KIND else None,
            )
            self.session.add(check)
            # One COMMIT per check, so each gets its own `created_at` — Postgres'
            # `now()` is transaction-start, so a single add_all would give them all
            # the identical timestamp and the ordering assertion below would be a
            # coin flip on the physical row order.
            self.session.commit()
            self.checks.append(check)
        self.run = Run(suite_id=suite.id, status="queued", triggered_by="test")
        self.session.add(self.run)
        self.session.commit()

    def poll(self) -> run_service.RunProgress:
        """Read progress the way the API process does — a separate session on its
        own connection, in its own transaction."""
        with SASession(bind=self.engine) as other:
            run = other.get(Run, self.run.id)
            assert run is not None
            return run_service.get_run_progress(other, run)

    def cancel_from_the_api(self) -> None:
        with SASession(bind=self.engine) as api:
            run = api.get(Run, self.run.id)
            assert run is not None
            run_service.cancel_run(api, run)

    def results(self) -> list[Result]:
        with SASession(bind=self.engine) as other:
            return list(other.scalars(select(Result).where(Result.run_id == self.run.id)))

    def close(self) -> None:
        self.session.close()
        with SASession(bind=self.engine) as cleanup:
            cleanup.execute(delete(Result).where(Result.run_id == self.run.id))
            cleanup.execute(delete(Run).where(Run.suite_id == self.suite_id))
            cleanup.execute(delete(Check).where(Check.suite_id == self.suite_id))
            cleanup.execute(delete(Suite).where(Suite.id == self.suite_id))
            cleanup.execute(delete(Connection).where(Connection.id == self.connection_id))
            cleanup.execute(delete(User).where(User.id == self.owner_id))
            cleanup.commit()


@pytest.fixture
def make_suite(_db_engine: Any) -> Iterator[Callable[[list[str]], _Fixture]]:
    built: list[_Fixture] = []

    def _make(kinds: list[str]) -> _Fixture:
        fixture = _Fixture(_db_engine, kinds)
        built.append(fixture)
        return fixture

    yield _make
    for fixture in built:
        fixture.close()


def test_comparison_checks_resolve_one_at_a_time_to_a_concurrent_poller(
    make_suite: Callable[[list[str]], _Fixture],
) -> None:
    """A comparison check is its own publishable phase, so a poller on a separate
    connection watches `completed_checks` climb 0 → 1 during the run.

    This is the assertion that fails against commit-once-at-the-end, and
    `comparison` is the kind that carries it: its executor only reads, so its rows
    can be published the moment they exist.
    """
    fx = make_suite(["comparison", "comparison"])
    seen: list[int] = []

    def _compare(check: Check) -> CheckOutcome:
        seen.append(fx.poll().completed_checks)
        return _ok(check)

    run_service.execute_run(
        fx.session,
        run=fx.run,
        checks=fx.checks,
        runner=_Runner(),
        table="T",
        comparison_executor=_compare,
    )

    # Before check 0: nothing resolved. Before check 1: check 0's row is COMMITTED
    # and visible to another transaction — the whole point of #318.
    assert seen == [0, 1]
    assert fx.run.status == "succeeded"
    assert fx.poll().completed_checks == 2


def test_earlier_phases_are_visible_before_the_expectation_batch_runs(
    make_suite: Callable[[list[str]], _Fixture],
) -> None:
    """A mixed suite: two comparison checks land as increments, then three
    expectations land together. The poll taken inside `run_checks` sees exactly the
    comparison pair — proof both that earlier phases really committed and that the
    batch's own checks are not counted before they resolve."""
    fx = make_suite(["comparison", "comparison", "expectation", "expectation", "expectation"])
    at_batch: list[int] = []

    runner = _Runner(before=lambda: at_batch.append(fx.poll().completed_checks))
    run_service.execute_run(
        fx.session,
        run=fx.run,
        checks=fx.checks,
        runner=runner,
        table="T",
        comparison_executor=_ok,
    )

    assert at_batch == [2]  # the two comparison checks, and only those
    final = fx.poll()
    assert (final.completed_checks, final.total_checks) == (5, 5)


def test_a_stateful_check_is_never_published_before_the_run_completes(
    make_suite: Callable[[list[str]], _Fixture],
) -> None:
    """The durability limit (#318 G1): a `schema_drift`/`anomaly` executor writes
    `monitor_baselines` through the run's session, so its result row must NOT be
    committed early — publishing it would make the baseline write durable on a run
    that may still fail.

    The stateful executor is itself the probe, and it is the right one: the
    stateful phases run LAST, so by the time it is called the expectation batch has
    already committed. A poll from the second call must still see only that batch —
    the first stateful check resolved, and was deliberately not published.
    """
    fx = make_suite(["schema_drift", "expectation", "anomaly"])
    seen: list[int] = []

    def _stateful(check: Check) -> CheckOutcome:
        seen.append(fx.poll().completed_checks)
        return _ok(check)

    run_service.execute_run(
        fx.session,
        run=fx.run,
        checks=fx.checks,
        runner=_Runner(),
        table="T",
        stateful_monitor_executor=_stateful,
    )

    # 1 = the expectation batch, on both calls. The second `1` is the assertion:
    # the first stateful check had resolved by then and stayed invisible.
    assert seen == [1, 1]
    assert fx.poll().completed_checks == 3  # all three land with the terminal commit


def test_expectation_only_suite_stays_at_zero_until_the_atomic_batch_lands(
    make_suite: Callable[[list[str]], _Fixture],
) -> None:
    """The honest limit (#318): GX resolves a suite of expectations in ONE step, so
    there is no increment to report and the poll legitimately reads 0/3 for the
    whole run. `elapsed_ms` is what tells a viewer it is alive — this is exactly
    the state the drawer must not render as a 0% bar."""
    fx = make_suite(["expectation"] * 3)
    mid: list[run_service.RunProgress] = []

    runner = _Runner(before=lambda: mid.append(fx.poll()))
    run_service.execute_run(fx.session, run=fx.run, checks=fx.checks, runner=runner, table="T")

    (during,) = mid
    assert during.completed_checks == 0
    assert during.run.status == "running"
    # Running, nothing resolved, but demonstrably alive and measured.
    assert during.elapsed_ms is not None and during.elapsed_ms >= 0
    assert fx.poll().completed_checks == 3


def test_a_failure_after_a_committed_phase_leaves_no_results(
    make_suite: Callable[[list[str]], _Fixture],
) -> None:
    """The terminal contract survives per-phase commits: the comparison check's row
    was committed, then the expectation batch raised — the run ends `failed` with
    ZERO results, exactly as before #318. Without `discard_run_results` this run
    would carry one orphan `pass` row on its own detail page.

    A comparison check, because it is the kind whose phase actually publishes;
    with a stateful one nothing would have been committed to discard and the test
    would prove nothing.
    """
    fx = make_suite(["comparison", "expectation"])

    run_service.execute_run(
        fx.session,
        run=fx.run,
        checks=fx.checks,
        runner=_Runner(raises=RuntimeError("warehouse unreachable")),
        table="T",
        comparison_executor=_ok,
    )

    assert fx.run.status == "failed"
    assert fx.run.failure_reason
    assert fx.results() == []
    assert fx.poll().completed_checks == 0


def test_a_cancel_after_a_committed_phase_leaves_no_results(
    make_suite: Callable[[list[str]], _Fixture],
) -> None:
    """Same for cancellation, from the direction it really happens: the API process
    commits `cancelled` on its own connection while the worker is between phases.
    The worker notices at its next pre-commit check and clears the rows the earlier
    phase had already committed."""
    fx = make_suite(["comparison", "comparison"])
    calls = 0

    def _compare(check: Check) -> CheckOutcome:
        nonlocal calls
        calls += 1
        if calls == 2:  # the user hits Cancel between phase 1 and phase 2
            fx.cancel_from_the_api()
        return _ok(check)

    run_service.execute_run(
        fx.session,
        run=fx.run,
        checks=fx.checks,
        runner=_Runner(),
        table="T",
        comparison_executor=_compare,
    )

    assert fx.run.status == "cancelled"
    assert fx.results() == []


def test_a_cancel_between_the_last_phase_and_the_terminal_flip_is_honoured(
    make_suite: Callable[[list[str]], _Fixture],
) -> None:
    """The last phase's commit and the succeeded-flip are two transactions, so a
    cancel can land between them. Without the second pre-flip check the run would
    overwrite `cancelled` with `succeeded` and keep its results.

    Hooked on the session's own `after_commit` rather than inside the executor,
    because an executor-time cancel is caught by that phase's *own* pre-commit
    check — it would exercise the wrong guard and pass with the pre-flip one
    deleted. The window this test is about opens only once the last phase has
    already committed: commit 1 is the `running` flip, commit 2 is the one and
    only phase, and the cancel lands right after it."""
    fx = make_suite(["comparison"])
    commits = 0

    @event.listens_for(fx.session, "after_commit")
    def _cancel_after_the_phase_commit(session: SASession) -> None:
        nonlocal commits
        commits += 1
        if commits == 2:  # 1 = the 'running' flip, 2 = the only phase's rows
            fx.cancel_from_the_api()

    try:
        run_service.execute_run(
            fx.session,
            run=fx.run,
            checks=fx.checks,
            runner=_Runner(),
            table="T",
            comparison_executor=_ok,
        )
    finally:
        event.remove(fx.session, "after_commit", _cancel_after_the_phase_commit)

    assert commits >= 2, "the phase never committed — this test would be vacuous"
    assert fx.run.status == "cancelled"
    assert fx.results() == []


def test_a_cancel_racing_the_terminal_flip_itself_still_wins(
    make_suite: Callable[[list[str]], _Fixture],
) -> None:
    """The narrower window the previous test cannot reach (#318 G4).

    A check-then-set can be beaten at any distance: the cancel here commits
    *after* the pre-flip `_cancelled_mid_run` read and *before* the flip's own
    statement, so no amount of re-checking closes it — only the predicate
    travelling with the write does. `_mark_succeeded` issues
    ``UPDATE … WHERE status = 'running'``, matches nothing, and the user's
    200-confirmed cancel survives instead of being silently overwritten with a
    `succeeded` run holding a full set of results.

    Hooked on `do_orm_execute` so the cancel lands in exactly that gap; the guard
    at the top keeps it to the flip statement and to one firing, so the listener
    cannot recurse through the discard DELETE that follows.
    """
    fx = make_suite(["comparison"])
    fired = 0

    @event.listens_for(fx.session, "do_orm_execute")
    def _cancel_just_before_the_flip(state: Any) -> None:
        nonlocal fired
        if fired or not state.is_update or not state.statement.is_dml:
            return
        fired += 1
        fx.cancel_from_the_api()

    try:
        run_service.execute_run(
            fx.session,
            run=fx.run,
            checks=fx.checks,
            runner=_Runner(),
            table="T",
            comparison_executor=_ok,
        )
    finally:
        event.remove(fx.session, "do_orm_execute", _cancel_just_before_the_flip)

    assert fired == 1, "the flip UPDATE never ran — this test would be vacuous"
    assert fx.run.status == "cancelled"
    assert fx.results() == []


def test_results_are_listed_in_check_order_not_execution_order(
    make_suite: Callable[[list[str]], _Fixture],
) -> None:
    """Per-phase commits give `results.created_at` real (execution-order) meaning,
    which would silently re-sort the run-detail table by *engine*.

    The suite is authored expectation-first, comparison-second — and the run
    executes them the other way round, because comparison is the first phase. So
    result `created_at` and check order genuinely disagree here, which is what
    makes the assertion able to fail. `list_results` orders by the CHECK, so the
    display keeps the author's order and matches the progress list row for row."""
    fx = make_suite(["expectation", "comparison"])

    run_service.execute_run(
        fx.session,
        run=fx.run,
        checks=fx.checks,
        runner=_Runner(),
        table="T",
        comparison_executor=_ok,
    )

    expected = [c.id for c in fx.checks]
    assert [r.check_id for r in run_service.list_results(fx.session, fx.run.id)] == expected
    assert [c.check_id for c in fx.poll().checks] == expected
