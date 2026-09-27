"""Batched schedule dispatch (#1999) — batching, DST, no-backfill, budget, publish failures."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import event, select
from structlog.testing import capture_logs

from backend.app.db.models import Connection, Run, Schedule, Suite, User
from backend.app.services import cron, run_dispatch, schedule_dispatch

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


def _suite(db_session: Any, *, target: dict[str, Any] | None = None) -> Suite:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:6]}@example.com")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "a"},
        secret_ref="kv-sf",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(
        name="nightly",
        connection_id=conn.id,
        created_by=owner.id,
        target=target if target is not None else {"table": "ORDERS"},
    )
    db_session.add(suite)
    db_session.commit()
    return suite


def _schedules(
    db_session: Any,
    suite: Suite,
    count: int,
    *,
    cron_expr: str = "0 * * * *",
    timezone: str = "UTC",
    next_run_at: datetime = NOW - timedelta(minutes=1),
) -> list[Schedule]:
    rows = [
        Schedule(
            suite_id=suite.id,
            cron=cron_expr,
            timezone=timezone,
            next_run_at=next_run_at - timedelta(seconds=i),
            created_by=suite.created_by,
        )
        for i in range(count)
    ]
    db_session.add_all(rows)
    db_session.commit()
    return rows


def _runs(db_session: Any) -> list[Run]:
    return list(db_session.scalars(select(Run).where(Run.triggered_by.like("schedule:%"))))


def _statements(db_session: Any, call: Any) -> int:
    seen: list[str] = []

    def before(_conn: Any, _cursor: Any, statement: str, *_a: Any) -> None:
        seen.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", before)
    try:
        call()
    finally:
        event.remove(bind, "before_cursor_execute", before)
    return len(seen)


def test_every_due_schedule_fires_once_across_batch_boundaries(
    db_session: Any, stub_run_dispatch: list[str]
) -> None:
    suite = _suite(db_session)
    schedules = _schedules(db_session, suite, 7)

    summary = schedule_dispatch.dispatch_due_schedules(db_session, now=NOW, batch_size=3)

    assert summary["due"] == 7 and summary["dispatched"] == 7 and summary["residual"] == 0
    runs = _runs(db_session)
    assert sorted(r.triggered_by or "" for r in runs) == sorted(
        f"schedule:{s.id}" for s in schedules
    )
    assert sorted(stub_run_dispatch) == sorted(str(r.id) for r in runs)
    assert all(r.celery_task_id == f"task-{r.id}" for r in runs)
    for sched in schedules:
        db_session.refresh(sched)
        assert sched.next_run_at == datetime(2026, 6, 15, 13, 0, tzinfo=UTC)
        assert sched.last_run_at == NOW


def test_round_trips_do_not_grow_with_the_due_set(
    db_session: Any, stub_run_dispatch: list[str]
) -> None:
    """The per-schedule statement loop is what #1999 removed: 3 due and 40 due must
    cost the same number of statements when both fit in one batch.
    """
    suite = _suite(db_session)
    _schedules(db_session, suite, 3)
    few = _statements(
        db_session, lambda: schedule_dispatch.dispatch_due_schedules(db_session, now=NOW)
    )

    _schedules(db_session, suite, 40, next_run_at=NOW + timedelta(minutes=30))
    later = NOW + timedelta(minutes=31)
    many = _statements(
        db_session, lambda: schedule_dispatch.dispatch_due_schedules(db_session, now=later)
    )

    assert len(_runs(db_session)) == 43
    assert few == many


def test_mixed_batch_disables_skips_and_fires(
    db_session: Any, stub_run_dispatch: list[str]
) -> None:
    good = _suite(db_session)
    targetless = _suite(db_session, target={})
    fire = _schedules(db_session, good, 2)
    impossible = _schedules(db_session, good, 1, cron_expr="0 0 30 2 *")[0]
    skipped = _schedules(db_session, targetless, 1)[0]

    summary = schedule_dispatch.dispatch_due_schedules(db_session, now=NOW)

    assert summary == {
        "due": 4,
        "dispatched": 2,
        "skipped_target": 1,
        "dispatch_failed": 0,
        "disabled": 1,
        "residual": 0,
    }
    assert sorted(r.triggered_by or "" for r in _runs(db_session)) == sorted(
        f"schedule:{s.id}" for s in fire
    )
    db_session.refresh(impossible)
    db_session.refresh(skipped)
    assert impossible.enabled is False
    assert impossible.next_run_at == NOW - timedelta(minutes=1)  # never advanced: it can't fire
    assert skipped.enabled is True and skipped.next_run_at > NOW


def test_same_cron_in_different_timezones_is_advanced_per_timezone(
    db_session: Any, stub_run_dispatch: list[str]
) -> None:
    """The next fire is memoised per tick; the key must include the timezone. US DST
    starts 2026-03-08, so 09:00 New York is 14:00 UTC before it and 13:00 UTC after.
    """
    now = datetime(2026, 3, 7, 15, 0, tzinfo=UTC)
    suite = _suite(db_session)
    utc = _schedules(db_session, suite, 1, cron_expr="0 9 * * *", next_run_at=now)[0]
    nyc = _schedules(
        db_session, suite, 1, cron_expr="0 9 * * *", timezone="America/New_York", next_run_at=now
    )[0]

    schedule_dispatch.dispatch_due_schedules(db_session, now=now)

    db_session.refresh(utc)
    db_session.refresh(nyc)
    assert utc.next_run_at == datetime(2026, 3, 8, 9, 0, tzinfo=UTC)
    assert nyc.next_run_at == datetime(2026, 3, 8, 13, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("cron_expr", "now"),
    [
        # Spring forward: 02:30 does not exist in New York on 2026-03-08.
        ("30 2 * * *", datetime(2026, 3, 8, 6, 0, tzinfo=UTC)),
        # Fall back: 01:30 happens twice in New York on 2026-11-01.
        ("30 1 * * *", datetime(2026, 11, 1, 5, 0, tzinfo=UTC)),
        ("30 1 * * *", datetime(2026, 11, 1, 5, 45, tzinfo=UTC)),
    ],
)
def test_dst_transitions_match_the_cron_service(
    db_session: Any, stub_run_dispatch: list[str], cron_expr: str, now: datetime
) -> None:
    suite = _suite(db_session)
    sched = _schedules(
        db_session, suite, 1, cron_expr=cron_expr, timezone="America/New_York", next_run_at=now
    )[0]

    schedule_dispatch.dispatch_due_schedules(db_session, now=now)

    db_session.refresh(sched)
    assert sched.next_run_at == cron.next_fire(cron_expr, "America/New_York", after=now)
    assert sched.next_run_at > now


def test_no_backfill_for_stale_schedules_in_one_batch(
    db_session: Any, stub_run_dispatch: list[str]
) -> None:
    suite = _suite(db_session)
    stale = _schedules(db_session, suite, 3, next_run_at=NOW - timedelta(hours=6))

    schedule_dispatch.dispatch_due_schedules(db_session, now=NOW)
    second = schedule_dispatch.dispatch_due_schedules(db_session, now=NOW)

    assert len(_runs(db_session)) == 3  # one per schedule, not one per missed hour
    assert second["due"] == 0
    for sched in stale:
        db_session.refresh(sched)
        assert sched.next_run_at == datetime(2026, 6, 15, 13, 0, tzinfo=UTC)


def test_budget_stops_the_tick_and_reports_the_residual(
    db_session: Any, stub_run_dispatch: list[str]
) -> None:
    suite = _suite(db_session)
    _schedules(db_session, suite, 5)
    ticks = iter([0.0, 100.0, 100.0])

    with capture_logs() as logs:
        summary = schedule_dispatch.dispatch_due_schedules(
            db_session, now=NOW, batch_size=2, budget_s=45.0, clock=lambda: next(ticks)
        )

    assert summary["due"] == 2 and summary["dispatched"] == 2 and summary["residual"] == 3
    exhausted = [e for e in logs if e["event"] == "schedules_dispatch_budget_exhausted"]
    assert len(exhausted) == 1 and exhausted[0]["residual"] == 3

    rest = schedule_dispatch.dispatch_due_schedules(db_session, now=NOW)
    assert rest["due"] == 3 and rest["residual"] == 0
    assert len(_runs(db_session)) == 5


def test_one_failed_publish_fails_only_its_run(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite = _suite(db_session)
    _schedules(db_session, suite, 3)
    doomed: list[str] = []

    def _flaky(run_id: object) -> str:
        if not doomed:
            doomed.append(str(run_id))
            raise RuntimeError("broker down")
        return f"task-{run_id}"

    monkeypatch.setattr(run_dispatch, "dispatch_run", _flaky)

    summary = schedule_dispatch.dispatch_due_schedules(db_session, now=NOW)

    assert summary["dispatched"] == 2 and summary["dispatch_failed"] == 1
    by_id = {str(r.id): r for r in _runs(db_session)}
    failed = by_id.pop(doomed[0])
    assert failed.status == "failed"
    assert failed.failure_reason == run_dispatch.DISPATCH_FAILED_REASON
    assert failed.finished_at is not None and failed.celery_task_id is None
    assert all(r.status == "queued" and r.celery_task_id == f"task-{r.id}" for r in by_id.values())


def test_a_failed_publish_does_not_overwrite_a_run_cancelled_meanwhile(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite = _suite(db_session)
    _schedules(db_session, suite, 1)

    def _cancel_then_fail(run_id: object) -> str:
        run = db_session.get(Run, run_id)
        run.status = "cancelled"
        db_session.commit()
        raise RuntimeError("broker down")

    monkeypatch.setattr(run_dispatch, "dispatch_run", _cancel_then_fail)

    schedule_dispatch.dispatch_due_schedules(db_session, now=NOW)

    (run,) = _runs(db_session)
    db_session.refresh(run)
    assert run.status == "cancelled"


def test_a_down_broker_fails_the_batch_fast_and_ends_the_tick(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each failed publish costs a full retry cycle, so after BROKER_DOWN_AFTER in a row
    the rest of the batch is failed without trying and no further batch is claimed.
    """
    suite = _suite(db_session)
    _schedules(db_session, suite, 8)
    attempts: list[object] = []

    def _down(run_id: object) -> str:
        attempts.append(run_id)
        raise RuntimeError("broker down")

    monkeypatch.setattr(run_dispatch, "dispatch_run", _down)

    with capture_logs() as logs:
        summary = schedule_dispatch.dispatch_due_schedules(db_session, now=NOW, batch_size=5)

    assert len(attempts) == schedule_dispatch.BROKER_DOWN_AFTER
    assert summary["due"] == 5 and summary["dispatch_failed"] == 5
    assert summary["residual"] == 3  # left due for the next tick, not failed
    assert all(r.status == "failed" for r in _runs(db_session))
    assert any(e["event"] == "schedules_dispatch_stopped_broker_down" for e in logs)
