"""A run that does not succeed must strand nothing in `monitor_baselines` (#318 G1).

`monitor_baseline.insert_baseline_if_absent` states the contract outright — "rides
the caller's transaction, so a rolled-back run strands nothing" — and per-phase
commits are exactly the kind of change that breaks a contract like that without
touching the module that declares it. The stateful executors (`schema_drift`,
`anomaly`) write baselines through the RUN's session, so committing their result
rows early would make those writes durable too.

What that costs, concretely, is why this is a correctness test and not tidiness:

* an `anomaly` observation from a failed run sits in the rolling z-score window
  forever, and every retry of that run appends **another** one — a handful of
  retries can flatten a real anomaly by inflating the window's spread;
* a first `schema_drift` capture from a run that never completed becomes the
  reference baseline every later run is diffed against, so the drift that run was
  about is silently adopted as normal.

The fix is structural rather than compensating: those phases are yielded last and
`publishable=False`, so their rows ride the terminal commit alongside the baseline
writes they belong to. These tests assert the observable end state — is there a
baseline row after a run that failed / was cancelled — on a real database, because
the whole question is what a *commit* did.

Skips without `TEST_DATABASE_URL` (via `_db_engine`).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as SASession

from backend.app.datasources.base import CheckOutcome, CheckSpec, SuiteOutcome
from backend.app.db.models import Check, Connection, MonitorBaseline, Result, Run, Suite, User
from backend.app.services import monitor_baseline, run_service

_ANOMALY_BASELINE = {"observations": [{"ts": "2026-08-14T00:00:00+00:00", "value": 1.0}]}


class _Runner:
    """A GX-shaped runner for the expectation batch; optionally explodes."""

    supported_monitor_kinds: frozenset[str] = frozenset()

    def __init__(self, *, raises: Exception | None = None) -> None:
        self._raises = raises

    def run_checks(
        self,
        *,
        table: str,
        schema: str | None,
        checks: list[CheckSpec],
        index_columns: list[str] | None = None,
    ) -> SuiteOutcome:
        if self._raises is not None:
            raise self._raises
        return SuiteOutcome(
            success=True,
            checks=[
                CheckOutcome(expectation_type=c.expectation_type, success=True) for c in checks
            ],
        )


class _Fixture:
    """A suite with an `anomaly`, a `comparison` and an expectation, committed for real.

    The `comparison` check is not decoration: it is a **publishable** phase, so it
    issues a commit. That is what makes this fixture able to catch the subtle half
    of the fix — with the stateful phase ordered first, its staged baseline write
    would be flushed by the comparison phase's commit even though the stateful
    phase itself was marked unpublishable, because a commit is transaction-wide.
    Ordering and publishability are two guards and this needs both.
    """

    def __init__(self, engine: Any) -> None:
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
        self.session.flush()
        self.stateful = Check(
            suite_id=suite.id, name="drift", kind="anomaly", expectation_type="", config={}
        )
        self.comparison = Check(
            suite_id=suite.id,
            name="recon",
            kind="comparison",
            expectation_type="",
            config={},
            source_connection_id=conn.id,
        )
        self.expectation = Check(
            suite_id=suite.id, name="notnull", kind="expectation", expectation_type="x", config={}
        )
        self.session.add_all([self.stateful, self.comparison, self.expectation])
        self.session.flush()
        self.run = Run(suite_id=suite.id, status="queued", triggered_by="test")
        self.session.add(self.run)
        self.session.commit()
        self.owner_id, self.connection_id, self.suite_id = owner.id, conn.id, suite.id
        self.checks = [self.stateful, self.comparison, self.expectation]

    def baseline_rows(self) -> list[MonitorBaseline]:
        """Read on a SEPARATE connection — an uncommitted write is invisible there,
        which is precisely the distinction this whole file is about."""
        with SASession(bind=self.engine) as other:
            return list(
                other.scalars(
                    select(MonitorBaseline).where(MonitorBaseline.check_id == self.stateful.id)
                )
            )

    def cancel_from_the_api(self) -> None:
        with SASession(bind=self.engine) as api:
            run = api.get(Run, self.run.id)
            assert run is not None
            run_service.cancel_run(api, run)

    def close(self) -> None:
        self.session.close()
        with SASession(bind=self.engine) as cleanup:
            cleanup.execute(
                delete(MonitorBaseline).where(MonitorBaseline.check_id == self.stateful.id)
            )
            cleanup.execute(delete(Result).where(Result.run_id == self.run.id))
            cleanup.execute(delete(Run).where(Run.suite_id == self.suite_id))
            cleanup.execute(delete(Check).where(Check.suite_id == self.suite_id))
            cleanup.execute(delete(Suite).where(Suite.id == self.suite_id))
            cleanup.execute(delete(Connection).where(Connection.id == self.connection_id))
            cleanup.execute(delete(User).where(User.id == self.owner_id))
            cleanup.commit()


@pytest.fixture
def suite_fixture(_db_engine: Any) -> Iterator[_Fixture]:
    fixture = _Fixture(_db_engine)
    yield fixture
    fixture.close()


def _ok(check: Check) -> CheckOutcome:
    return CheckOutcome(expectation_type=check.expectation_type, success=True)


def _baseline_writing_executor(fx: _Fixture) -> Callable[[Check], CheckOutcome]:
    """Stands in for the real `schema_drift`/`anomaly` executors in the one respect
    that matters here: it writes a baseline through the RUN's session, exactly as
    `build_anomaly_executor` does via `insert_baseline_if_absent`."""

    def _executor(check: Check) -> CheckOutcome:
        monitor_baseline.insert_baseline_if_absent(
            fx.session, check_id=check.id, kind=check.kind, baseline=_ANOMALY_BASELINE
        )
        return CheckOutcome(expectation_type=check.expectation_type, success=True)

    return _executor


def test_a_failed_run_leaves_no_baseline(suite_fixture: _Fixture) -> None:
    """The headline: the stateful check ran and wrote its baseline, then the
    expectation batch raised. Nothing may survive — not the result row, and not the
    baseline that would poison every later run of that check."""
    fx = suite_fixture

    run_service.execute_run(
        fx.session,
        run=fx.run,
        checks=fx.checks,
        runner=_Runner(raises=RuntimeError("warehouse unreachable")),
        table="T",
        comparison_executor=_ok,
        stateful_monitor_executor=_baseline_writing_executor(fx),
    )

    assert fx.run.status == "failed"
    assert fx.baseline_rows() == []


def test_a_cancelled_run_leaves_no_baseline(suite_fixture: _Fixture) -> None:
    """Same for a cancel that lands while the run is executing."""
    fx = suite_fixture
    write_baseline = _baseline_writing_executor(fx)

    def _executor(check: Check) -> CheckOutcome:
        outcome = write_baseline(check)
        fx.cancel_from_the_api()  # the user hits Cancel with the baseline staged
        return outcome

    run_service.execute_run(
        fx.session,
        run=fx.run,
        checks=fx.checks,
        runner=_Runner(),
        table="T",
        comparison_executor=_ok,
        stateful_monitor_executor=_executor,
    )

    assert fx.run.status == "cancelled"
    assert fx.baseline_rows() == []


def test_a_succeeded_run_DOES_persist_its_baseline(suite_fixture: _Fixture) -> None:
    """The other half, and the reason this can't be fixed by simply never writing:
    a run that completes must leave its baseline behind, or the next run has no
    history to score against and `anomaly` never leaves its cold start."""
    fx = suite_fixture

    run_service.execute_run(
        fx.session,
        run=fx.run,
        checks=fx.checks,
        runner=_Runner(),
        table="T",
        comparison_executor=_ok,
        stateful_monitor_executor=_baseline_writing_executor(fx),
    )

    assert fx.run.status == "succeeded"
    rows = fx.baseline_rows()
    assert len(rows) == 1
    assert rows[0].baseline == _ANOMALY_BASELINE
