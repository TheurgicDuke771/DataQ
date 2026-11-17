"""`result_status_hourly` (#2010): what goes into it, what comes out, and when it is skipped.

The dashboard's own equivalence tests (`test_dashboard_one_pass.py`) compare the summary
with a per-window reference in three states of the table. These cover the table itself.
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import event, func, select, text, true
from sqlalchemy.orm import sessionmaker
from structlog.testing import capture_logs

from backend.app.db.models import (
    Check,
    Connection,
    Result,
    ResultStatusHourly,
    Run,
    Suite,
    User,
)
from backend.app.services import check_service, result_rollup, rollup, run_service, suite_service
from backend.app.worker import tasks
from backend.app.worker.celery_app import celery_app
from backend.tests.support.scratch_db import scratch_engine

NOW = datetime(2026, 11, 10, 14, 37, 12, tzinfo=UTC)
SINCE = NOW - timedelta(days=7)  # mid-hour, so both window edges are part-hours
PREV = SINCE - timedelta(days=7)


def _suite(db: Any, name: str = "s") -> Suite:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:6]}@example.com")
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
    suite = Suite(name=name, connection_id=conn.id, created_by=owner.id, target={"table": "T"})
    db.add(suite)
    db.flush()
    return suite


def _check(db: Any, suite: Suite) -> Check:
    check = Check(
        suite_id=suite.id, name=f"chk-{uuid.uuid4().hex[:6]}", expectation_type="e", config={}
    )
    db.add(check)
    db.flush()
    return check


def _run(
    db: Any,
    suite: Suite,
    at: datetime,
    statuses: list[str],
    *,
    status: str = "succeeded",
    check: Check | None = None,
) -> Run:
    run = Run(suite_id=suite.id, status=status, created_at=at)
    db.add(run)
    db.flush()
    for result_status in statuses:
        db.add(
            Result(
                run_id=run.id,
                check_id=(check or _check(db, suite)).id,
                status=result_status,
                created_at=at,
            )
        )
    db.flush()
    return run


def _from_results(db: Any) -> tuple[dict[str, int], dict[str, int]]:
    """The histogram straight from `results`: what the summary must always equal."""

    def window(low: datetime, high: datetime | None) -> dict[str, int]:
        stmt = (
            select(Result.status, func.count())
            .join(Run, Result.run_id == Run.id)
            .where(Run.status == "succeeded", Result.created_at >= low)
            .group_by(Result.status)
        )
        if high is not None:
            stmt = stmt.where(Result.created_at < high)
        return dict(db.execute(stmt).tuples().all())

    return window(SINCE, None), window(PREV, SINCE)


def _read(db: Any) -> tuple[dict[str, int], dict[str, int]]:
    return result_rollup.status_counts(
        db, visible=lambda _column: true(), since=SINCE, prev_since=PREV
    )


def _table(db: Any) -> dict[tuple[uuid.UUID, datetime, str], int]:
    db.expire_all()
    return {
        (row.suite_id, row.hour, row.status): row.n
        for row in db.scalars(select(ResultStatusHourly))
        if row.n
    }


def _table_from_results(db: Any) -> dict[tuple[uuid.UUID, datetime, str], int]:
    """What the table must hold: the results of the runs marked as added, by hour."""
    rows = db.execute(text("""
            SELECT run.suite_id, date_trunc('hour', r.created_at, 'UTC'), r.status, count(*)
            FROM results r JOIN runs run ON run.id = r.run_id
            WHERE run.results_rolled_up_at IS NOT NULL
            GROUP BY 1, 2, 3
            """))
    return {(suite_id, hour, status): n for suite_id, hour, status, n in rows}


def _seed_every_edge(db: Any) -> list[Suite]:
    """Results in each place a window boundary could miscount: the part-hour either side of
    ``SINCE`` and of ``PREV``, the whole hours beside them, both windows' middles, before
    ``PREV``, and runs that are not succeeded."""
    one, two = _suite(db, "one"), _suite(db, "two")
    minute = timedelta(minutes=1)
    for moment, statuses in [
        (NOW - timedelta(hours=1), ["pass", "warn"]),
        (SINCE + minute, ["pass", "fail"]),  # the part-hour, current side
        (SINCE - minute, ["critical"]),  # the part-hour, previous side
        (SINCE.replace(minute=0, second=0), ["skip"]),  # the bucket's first instant
        (SINCE.replace(minute=0, second=0) - minute, ["error"]),  # the whole hour before
        (SINCE.replace(minute=0, second=0) + timedelta(hours=1), ["pass"]),  # and after
        (SINCE - timedelta(days=3), ["pass", "pass", "fail"]),
        (PREV + minute, ["warn"]),  # the low part-hour, inside
        (PREV - minute, ["critical"]),  # the low part-hour, outside
        (PREV.replace(minute=0, second=0) + timedelta(hours=1), ["fail"]),
        (PREV - timedelta(days=2), ["pass"]),
    ]:
        _run(db, one, moment, statuses)
        _run(db, two, moment, statuses[:1])
    _run(db, one, NOW - timedelta(hours=2), ["critical"], status="failed")
    _run(db, one, NOW - timedelta(hours=3), ["critical"], status="running")
    db.commit()
    return [one, two]


@pytest.mark.parametrize("added", [0, 5, None], ids=["none added", "five added", "all added"])
def test_a_read_equals_results_whatever_is_in_the_table(db_session: Any, added: int | None) -> None:
    _seed_every_edge(db_session)
    if added != 0:
        result_rollup.roll_up_pending(db_session, limit=added or 10_000)

    current, previous = _read(db_session)

    assert (current, previous) == _from_results(db_session)
    assert current and previous  # not vacuous
    assert _table(db_session) == _table_from_results(db_session)


def test_only_succeeded_runs_are_added_and_each_once(db_session: Any) -> None:
    suite = _suite(db_session)
    done = _run(db_session, suite, NOW, ["pass", "fail"])
    failed = _run(db_session, suite, NOW, ["critical"], status="failed")

    for _ in range(2):  # the second add finds the marker set and adds nothing
        result_rollup.roll_up_run(db_session, done.id)
    result_rollup.roll_up_run(db_session, failed.id)
    db_session.commit()

    hour = NOW.replace(minute=0, second=0)
    assert _table(db_session) == {(suite.id, hour, "pass"): 1, (suite.id, hour, "fail"): 1}
    db_session.refresh(done)
    db_session.refresh(failed)
    assert done.results_rolled_up_at is not None
    assert failed.results_rolled_up_at is None


def test_buckets_are_utc_hours_whatever_the_session_time_zone(db_session: Any) -> None:
    """A half-hour zone would otherwise cut the hour at :30 and split a bucket in two."""
    suite = _suite(db_session)
    db_session.execute(text("SET LOCAL TIME ZONE 'Asia/Kolkata'"))
    at = datetime(2026, 11, 10, 14, 10, tzinfo=UTC)
    _run(db_session, suite, at, ["pass"])
    _run(db_session, suite, at + timedelta(minutes=40), ["pass"])

    result_rollup.roll_up_pending(db_session)

    assert _table(db_session) == {(suite.id, at.replace(minute=0), "pass"): 2}


def test_a_failed_add_does_not_fail_the_run_and_is_retried_by_the_sweep(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite = _suite(db_session)
    run = _run(db_session, suite, SINCE + timedelta(days=1), ["pass"])
    db_session.commit()
    monkeypatch.setattr(result_rollup, "_ADD_ONE", text("SELECT 1 FROM no_such_table"))

    with capture_logs() as logs:
        result_rollup.roll_up_run(db_session, run.id)  # must not raise
    db_session.commit()  # the caller's transaction is still usable

    assert [e["event"] for e in logs] == ["result_rollup_failed"]
    assert _table(db_session) == {}
    assert _read(db_session) == _from_results(db_session) == ({"pass": 1}, {})
    assert result_rollup.roll_up_pending(db_session) == 1
    assert result_rollup.pending_count(db_session) == 0
    assert sum(_table(db_session).values()) == 1
    assert _read(db_session) == ({"pass": 1}, {})


def test_a_run_is_added_as_it_succeeds(db_session: Any) -> None:
    suite = _suite(db_session)
    run = Run(suite_id=suite.id, status="running", created_at=NOW)
    db_session.add(run)
    db_session.flush()
    db_session.add(Result(run_id=run.id, check_id=_check(db_session, suite).id, status="warn"))
    db_session.commit()

    assert run_service._mark_succeeded(db_session, run) is True

    db_session.refresh(run)
    assert run.results_rolled_up_at is not None
    assert list(_table(db_session).values()) == [1]


def test_a_run_that_lost_to_a_cancel_is_not_added(db_session: Any) -> None:
    suite = _suite(db_session)
    run = _run(db_session, suite, NOW, ["warn"], status="cancelled")
    db_session.commit()

    assert run_service._mark_succeeded(db_session, run) is False

    assert _table(db_session) == {}


def test_a_skipped_run_is_added(db_session: Any) -> None:
    suite = _suite(db_session)
    checks = [_check(db_session, suite), _check(db_session, suite)]
    run = Run(suite_id=suite.id, status="queued")
    db_session.add(run)
    db_session.commit()

    run_service.skip_run(db_session, run=run, checks=checks, reason="nothing to evaluate")

    assert list(_table(db_session).values()) == [2]
    assert {status for _suite_id, _hour, status in _table(db_session)} == {"skip"}


@pytest.mark.parametrize("bulk", [False, True], ids=["one check", "bulk"])
def test_deleting_a_check_takes_its_results_out(db_session: Any, bulk: bool) -> None:
    """`results.check_id` cascades, so the table would otherwise count results that are gone."""
    suite = _suite(db_session)
    doomed, kept = _check(db_session, suite), _check(db_session, suite)
    for days in (1, 2, 9):
        at = SINCE + timedelta(days=7 - days)
        _run(db_session, suite, at, ["fail", "fail"], check=doomed)
        _run(db_session, suite, at, ["pass"], check=kept)
    not_added = _run(db_session, suite, SINCE + timedelta(days=3), ["fail"], check=doomed)
    db_session.commit()
    result_rollup.roll_up_pending(db_session)
    not_added.results_rolled_up_at = None  # a run the table does not hold: nothing to take back
    db_session.execute(
        text("UPDATE result_status_hourly SET n = n - 1 WHERE hour = :h AND status = 'fail'"),
        {"h": (SINCE + timedelta(days=3)).replace(minute=0, second=0, microsecond=0)},
    )
    db_session.commit()
    assert _table(db_session) == _table_from_results(db_session)

    if bulk:
        check_service.bulk_delete_checks(db_session, suite.id, [doomed.id])
    else:
        check_service.delete_check(db_session, suite.id, doomed.id)
    db_session.commit()

    assert _table(db_session) == _table_from_results(db_session)
    current, previous = _read(db_session)
    assert (current, previous) == _from_results(db_session)
    assert "fail" not in current and "fail" not in previous
    assert current["pass"] == 2 and previous["pass"] == 1


def test_the_counts_go_with_their_suite(db_session: Any) -> None:
    doomed, kept = _suite(db_session, "doomed"), _suite(db_session, "kept")
    _run(db_session, doomed, NOW, ["pass"])
    _run(db_session, kept, NOW, ["fail"])
    result_rollup.roll_up_pending(db_session)

    suite_service.delete_suite(db_session, doomed.id)
    db_session.commit()

    assert {suite_id for suite_id, _hour, _status in _table(db_session)} == {kept.id}
    assert _read(db_session) == _from_results(db_session)


def test_a_read_sees_only_visible_suites_in_the_table(db_session: Any) -> None:
    mine, theirs = _suite(db_session, "mine"), _suite(db_session, "theirs")
    _run(db_session, mine, SINCE + timedelta(days=2), ["pass"])
    _run(db_session, theirs, SINCE + timedelta(days=2), ["critical"])
    result_rollup.roll_up_pending(db_session)

    owner = mine.created_by
    assert owner is not None
    current, _previous = result_rollup.status_counts(
        db_session,
        visible=lambda column: suite_service.accessible_suite_filter(column, owner),
        since=SINCE,
        prev_since=PREV,
    )

    assert current == {"pass": 1}


def _statements(db: Any, call: Any) -> list[str]:
    seen: list[str] = []

    def before(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            seen.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", before)
    try:
        call()
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", before)
    return seen


def test_with_a_large_backlog_a_read_counts_from_results_alone(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """History is still being added after an upgrade: the table would save nothing yet."""
    monkeypatch.setattr(result_rollup, "PENDING_MAX", 3)
    _seed_every_edge(db_session)

    statements = _statements(db_session, lambda: _read(db_session))

    assert len(statements) == 2
    assert "result_status_hourly" not in statements[1]
    assert _read(db_session) == _from_results(db_session)


def test_with_nothing_pending_results_are_read_for_the_part_hours_only(db_session: Any) -> None:
    _seed_every_edge(db_session)
    result_rollup.roll_up_pending(db_session)

    statements = _statements(db_session, lambda: _read(db_session))

    assert len(statements) == 2
    assert "result_status_hourly" in statements[1]
    # `results` is read for the two part-hours, and for the runs the table does not hold.
    assert statements[1].count("FROM results JOIN runs") == 2


def test_the_sweep_works_in_batches_and_reports_what_is_left(db_session: Any) -> None:
    suite = _suite(db_session)
    for hours in range(5):
        _run(db_session, suite, NOW - timedelta(hours=hours), ["pass"])
    db_session.commit()

    assert result_rollup.roll_up_pending(db_session, limit=2) == 2
    assert result_rollup.pending_count(db_session) == 3
    assert result_rollup.roll_up_pending(db_session, limit=2) == 2
    assert result_rollup.roll_up_pending(db_session, limit=2) == 1
    assert result_rollup.roll_up_pending(db_session, limit=2) == 0  # nothing left to claim
    assert sum(_table(db_session).values()) == 5


def test_the_sweep_task_drains_the_backlog_and_is_scheduled(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite = _suite(db_session)
    for hours in range(5):
        _run(db_session, suite, NOW - timedelta(hours=hours), ["pass"])
    db_session.commit()
    monkeypatch.setattr(result_rollup, "SWEEP_BATCH", 2)
    monkeypatch.setattr(tasks, "get_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    assert tasks.roll_up_results() == 0

    assert sum(_table(db_session).values()) == 5
    assert celery_app.conf.beat_schedule["roll-up-results"]["task"] == "roll_up_results"


def test_the_sweep_task_stops_at_its_budget_and_says_what_is_left(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite = _suite(db_session)
    for hours in range(5):
        _run(db_session, suite, NOW - timedelta(hours=hours), ["pass"])
    db_session.commit()
    monkeypatch.setattr(result_rollup, "SWEEP_BATCH", 2)
    monkeypatch.setattr(tasks, "_ROLL_UP_BUDGET_S", 0.0)
    monkeypatch.setattr(tasks, "get_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    # The task's logger is replaced, not captured: another test's logging setup can leave
    # `capture_logs` seeing nothing from a logger that was already built.
    said: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(tasks.log, "info", lambda event, **kw: said.append((event, kw)))

    assert tasks.roll_up_results() == 3

    assert said == [("result_rollup_backlog", {"remaining": 3})]


def _after_the_backlog_check(db: Any, hook: Any) -> Any:
    """Run ``hook`` once, right after `status_counts` asks how big the backlog is: the moment
    between its two statements."""
    fired: list[int] = []

    def after(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        if statement.startswith("SELECT count(*) FROM (SELECT 1 FROM runs") and not fired:
            fired.append(1)
            hook()

    event.listen(db.get_bind(), "after_cursor_execute", after)
    try:
        return _read(db), fired
    finally:
        event.remove(db.get_bind(), "after_cursor_execute", after)


def test_a_run_added_between_the_two_statements_is_counted_once(db_session: Any) -> None:
    """The sweep can add a run after the read asked about the backlog and before it counts."""
    _seed_every_edge(db_session)
    result_rollup.roll_up_pending(db_session, limit=5)
    expected = _from_results(db_session)

    counted, fired = _after_the_backlog_check(
        db_session, lambda: db_session.execute(result_rollup._ADD_PENDING, {"limit": 10_000})
    )

    assert fired and counted == expected


def test_a_run_that_succeeds_between_the_two_statements_is_still_counted(db_session: Any) -> None:
    """A run that succeeds, and fails to be added, after the backlog was asked about."""
    suite = _suite(db_session, "late")
    _run(db_session, suite, SINCE + timedelta(days=2), ["pass"])
    result_rollup.roll_up_pending(db_session)

    counted, fired = _after_the_backlog_check(
        db_session, lambda: _run(db_session, suite, SINCE + timedelta(days=3), ["fail", "fail"])
    )

    assert fired
    assert counted == ({"pass": 1, "fail": 2}, {}) == _from_results(db_session)


def test_the_table_counts_the_one_status_the_aggregates_count() -> None:
    """The table is built for succeeded runs; if the shared aggregates ever count more, it
    must be told, or the histogram would silently stop matching the other dashboard figures."""
    assert rollup.AGGREGATABLE_RUN_STATUSES == {"succeeded"}


def test_one_run_that_cannot_be_added_does_not_hold_back_the_rest(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite = _suite(db_session)
    runs = [_run(db_session, suite, NOW - timedelta(hours=h), ["pass"]) for h in range(4)]
    db_session.commit()
    poison = min(runs, key=lambda r: str(r.id))
    real = result_rollup.roll_up_run
    monkeypatch.setattr(result_rollup, "_ADD_PENDING", text("SELECT 1 FROM no_such_table"))
    monkeypatch.setattr(
        result_rollup,
        "roll_up_run",
        lambda session, run_id: run_id != poison.id and real(session, run_id),
    )
    said: list[str] = []
    monkeypatch.setattr(result_rollup.log, "exception", lambda event, **_kw: said.append(event))

    added = result_rollup.roll_up_pending(db_session)

    assert added == 3
    assert said == ["result_rollup_batch_failed"]
    assert sum(_table(db_session).values()) == 3
    assert result_rollup.pending_count(db_session) == 1
    assert _read(db_session) == _from_results(db_session)  # the run left out is still counted


def test_forgetting_a_check_locks_before_it_writes(db_session: Any) -> None:
    """Runs by id, then buckets by key, then the subtraction: the order every writer takes."""
    suite = _suite(db_session)
    check = _check(db_session, suite)
    _run(db_session, suite, NOW, ["pass"], check=check)
    result_rollup.roll_up_pending(db_session)

    written: list[str] = []

    def before(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        written.append(" ".join(statement.split()))

    event.listen(db_session.get_bind(), "before_cursor_execute", before)
    try:
        result_rollup.forget_checks(db_session, [check.id])
    finally:
        event.remove(db_session.get_bind(), "before_cursor_execute", before)

    written = [w for w in written if "FROM runs" in w[:40] or "result_status_hourly" in w[:60]]
    assert len(written) == 3
    assert written[0].startswith("SELECT id FROM runs") and written[0].endswith("FOR UPDATE")
    assert "ORDER BY h.suite_id, h.hour, h.status FOR UPDATE OF h" in written[1]
    assert written[2].startswith("UPDATE result_status_hourly")


def test_a_check_delete_waits_for_a_sweep_that_has_claimed_its_run() -> None:
    """Two real connections. The sweep has claimed a run and not committed; deleting a check
    of that run must wait, then subtract what the sweep added — not read the run as un-added,
    subtract nothing, and leave the sweep's counts for results that are about to be gone."""
    with scratch_engine("dataq_result_rollup_race") as engine:
        maker = sessionmaker(bind=engine)
        with maker() as setup:
            suite = _suite(setup, "race")
            check = _check(setup, suite)
            run = _run(setup, suite, NOW, ["pass", "pass"], check=check)
            setup.commit()
            suite_id, check_id, run_id = suite.id, check.id, run.id
            del suite, run

        sweep = maker()
        sweep.execute(result_rollup._ADD_ONE, {"run_id": run_id})  # claimed, not committed
        done = threading.Event()
        errors: list[BaseException] = []

        def delete_the_check() -> None:
            try:
                with maker() as deleter:
                    result_rollup.forget_checks(deleter, [check_id])
                    deleter.commit()
            except BaseException as exc:
                errors.append(exc)
            finally:
                done.set()

        worker = threading.Thread(target=delete_the_check, daemon=True)
        worker.start()
        try:
            assert not done.wait(1.5), "the delete did not wait for the sweep's claim"
            sweep.commit()
            assert done.wait(15), "the delete never finished"
        finally:
            sweep.rollback()
            sweep.close()
            worker.join(5)
        assert not errors, errors
        with maker() as check_session:
            held = check_session.scalar(
                select(func.coalesce(func.sum(ResultStatusHourly.n), 0)).where(
                    ResultStatusHourly.suite_id == suite_id
                )
            )
        assert held == 0
