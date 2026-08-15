"""Aggregates count only runs whose result set is complete (#318 G3).

Per-phase commits mean a `running` run has a genuinely partial set of result rows,
and a `failed`/`cancelled` one can retain stragglers — the run path's compensating
DELETE is best-effort, and the stuck-run reaper flips a dead worker's status
without owning the transaction that wrote them.

Before #318 every one of these readers was *accidentally* correct: a run that had
not finished had written nothing, so "all results in the window" and "all results
of a completed run" were the same set. `rollup.AGGREGATABLE_RUN_STATUSES` makes the
second one explicit, at each reader that presents numbers as a **suite's or an
asset's quality**.

Each test seeds the partial/stranded state directly rather than racing a real run:
the readers cannot tell how the rows got there, and a test that had to win a race
to be meaningful would be a flake.

Skips without `TEST_DATABASE_URL` (the `db_session` fixture).
"""

from __future__ import annotations

import uuid
from typing import Any

from backend.app.alerting import dedup
from backend.app.db.models import Check, Connection, Result, Run, Suite, User
from backend.app.services import dashboard_service, rollup, run_service


def _user(db: Any) -> User:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@ex.io")
    db.add(user)
    db.flush()
    return user


def _suite(db: Any, owner: User) -> Suite:
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
    suite = Suite(
        name=f"s-{uuid.uuid4().hex[:8]}",
        connection_id=conn.id,
        created_by=owner.id,
        target={"table": "T"},
    )
    db.add(suite)
    db.flush()
    return suite


def _run_with_results(db: Any, suite: Suite, *, status: str, result_statuses: list[str]) -> Run:
    """A run in ``status`` carrying ``result_statuses`` — including the combinations
    only a mid-flight or reaped run produces."""
    run = Run(suite_id=suite.id, status=status)
    db.add(run)
    db.flush()
    for s in result_statuses:
        check = Check(
            suite_id=suite.id, name=f"chk-{uuid.uuid4().hex[:6]}", expectation_type="e", config={}
        )
        db.add(check)
        db.flush()
        db.add(Result(run_id=run.id, check_id=check.id, status=s))
    db.commit()
    return run


def test_a_running_runs_partial_results_do_not_score_the_suite(db_session: Any) -> None:
    """The dashboard case, and the one a user would actually hit: a 30-check suite
    whose first committed phase happened to fail would otherwise render `critical`
    (1/1 fail) for the entire rest of the run — a red board caused by nothing but
    the order the phases ran in."""
    alice = _user(db_session)
    suite = _suite(db_session, alice)
    _run_with_results(db_session, suite, status="running", result_statuses=["critical"])

    summary = dashboard_service.dashboard_summary(db_session, user_id=alice.id, window_days=7)

    assert summary.kpis.health_score is None  # nothing countable yet, not 0.0
    assert summary.suite_performance == []


def test_a_completed_runs_results_still_score_the_suite(db_session: Any) -> None:
    """The control: the filter must exclude the incomplete, not everything. Without
    this pair a reader that returned nothing at all would pass the test above."""
    alice = _user(db_session)
    suite = _suite(db_session, alice)
    _run_with_results(db_session, suite, status="succeeded", result_statuses=["pass", "fail"])

    summary = dashboard_service.dashboard_summary(db_session, user_id=alice.id, window_days=7)

    assert summary.kpis.health_score == 75.0  # ADR 0005: one pass, one fail
    assert [s.name for s in summary.suite_performance] == [suite.name]


def test_stranded_rows_on_a_failed_run_are_not_counted(db_session: Any) -> None:
    """The reaped-worker / failed-discard case. A `failed` run is supposed to carry
    no results; when it does anyway, the aggregate must not adopt them — which is
    what lets the DELETE stay best-effort instead of load-bearing."""
    alice = _user(db_session)
    suite = _suite(db_session, alice)
    _run_with_results(db_session, suite, status="failed", result_statuses=["critical", "critical"])

    summary = dashboard_service.dashboard_summary(db_session, user_id=alice.id, window_days=7)

    assert summary.kpis.health_score is None


def test_status_histograms_opt_in_filter(db_session: Any) -> None:
    """The shared primitive both ways, since its default is deliberately *off*:
    the runs table shows one named run's live outcome and must keep seeing partials
    (#425), while the asset scorecard must not."""
    alice = _user(db_session)
    suite = _suite(db_session, alice)
    live = _run_with_results(db_session, suite, status="running", result_statuses=["pass", "fail"])

    unfiltered = rollup.status_histograms(db_session, [live.id])
    filtered = rollup.status_histograms(db_session, [live.id], complete_runs_only=True)

    assert unfiltered == {live.id: {"pass": 1, "fail": 1}}
    assert filtered == {}


def test_runs_table_still_shows_a_live_runs_partial_outcome(db_session: Any) -> None:
    """The counter-case for the whole finding: `check_outcome_counts` is what the
    runs table renders as `3 / 7 passed`, and blanking it mid-run would be a
    regression of #425 dressed up as a fix."""
    alice = _user(db_session)
    suite = _suite(db_session, alice)
    live = _run_with_results(db_session, suite, status="running", result_statuses=["pass", "fail"])

    assert run_service.check_outcome_counts(db_session, [live.id])[live.id] == (2, 1, "fail")
    assert run_service.check_outcome_counts(db_session, [live.id], complete_runs_only=True) == {}


def test_dedup_reads_no_per_check_signature_from_a_failed_run(db_session: Any) -> None:
    """A failed run is *defined* as having no per-check signature — that is what
    collapses it to the operational sentinel so two consecutive dead-worker
    failures dedup as one alert (#419). Reading stranded rows instead would give it
    a per-check signature, which either splits the operational alerts apart or
    matches a genuine prior alert and silences the one that mattered."""
    alice = _user(db_session)
    suite = _suite(db_session, alice)
    stranded = _run_with_results(db_session, suite, status="failed", result_statuses=["critical"])

    assert dedup._failing_ranks(db_session, stranded) == {dedup._OPERATIONAL_KEY: 2}


def test_dedup_still_reads_a_succeeded_runs_failing_checks(db_session: Any) -> None:
    """The control for the above — the ranks a real alert dedups on."""
    alice = _user(db_session)
    suite = _suite(db_session, alice)
    done = _run_with_results(db_session, suite, status="succeeded", result_statuses=["critical"])

    ranks = dedup._failing_ranks(db_session, done)
    assert list(ranks.values()) == [3]  # `critical`, keyed by check id
    assert dedup._OPERATIONAL_KEY not in ranks
