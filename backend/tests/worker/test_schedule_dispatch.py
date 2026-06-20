"""Dispatcher tests for scheduled suite runs (A7) — real Postgres.

Exercises `tasks._dispatch_due_schedules` against the DB (the `FOR UPDATE SKIP
LOCKED` due-scan + per-schedule advance can't be faithfully faked). `dispatch_run`
is stubbed by the autouse `stub_run_dispatch` conftest fixture, so no broker is
needed; its return value (the captured run-ids) lets us assert a run was queued.
Skips without TEST_DATABASE_URL.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from backend.app.db.models import Connection, Run, Schedule, Suite, User
from backend.app.worker import tasks

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


def _suite(db_session: Any, *, target: dict[str, Any] | None) -> Suite:
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
    suite = Suite(name="nightly", connection_id=conn.id, created_by=owner.id, target=target)
    db_session.add(suite)
    db_session.commit()
    return suite


def _schedule(
    db_session: Any,
    suite: Suite,
    *,
    next_run_at: datetime,
    cron: str = "0 * * * *",
    enabled: bool = True,
) -> Schedule:
    sched = Schedule(
        suite_id=suite.id,
        cron=cron,
        timezone="UTC",
        enabled=enabled,
        next_run_at=next_run_at,
        created_by=suite.created_by,
    )
    db_session.add(sched)
    db_session.commit()
    return sched


def _runs(db_session: Any, suite_id: uuid.UUID) -> list[Run]:
    return list(db_session.scalars(select(Run).where(Run.suite_id == suite_id)))


def test_due_schedule_fires_run_and_advances(db_session: Any, stub_run_dispatch: list[str]) -> None:
    suite = _suite(db_session, target={"table": "ORDERS"})
    sched = _schedule(db_session, suite, next_run_at=NOW - timedelta(minutes=1))

    summary = tasks._dispatch_due_schedules(db_session, now=NOW)

    assert summary["due"] == 1 and summary["dispatched"] == 1
    runs = _runs(db_session, suite.id)
    assert len(runs) == 1
    assert runs[0].status == "queued"
    assert runs[0].triggered_by == f"schedule:{sched.id}"
    assert str(runs[0].id) in stub_run_dispatch  # handed to the worker
    db_session.refresh(sched)
    assert sched.last_run_at is not None
    assert sched.next_run_at > NOW  # rolled forward out of the due window


def test_future_schedule_not_fired(db_session: Any) -> None:
    suite = _suite(db_session, target={"table": "ORDERS"})
    _schedule(db_session, suite, next_run_at=NOW + timedelta(hours=1))

    summary = tasks._dispatch_due_schedules(db_session, now=NOW)

    assert summary["due"] == 0
    assert _runs(db_session, suite.id) == []


def test_disabled_schedule_not_fired(db_session: Any) -> None:
    suite = _suite(db_session, target={"table": "ORDERS"})
    _schedule(db_session, suite, next_run_at=NOW - timedelta(hours=1), enabled=False)

    summary = tasks._dispatch_due_schedules(db_session, now=NOW)

    assert summary["due"] == 0
    assert _runs(db_session, suite.id) == []


def test_invalid_target_skips_run_but_advances(
    db_session: Any, stub_run_dispatch: list[str]
) -> None:
    """A targetless suite can't resolve a table — the schedule is rolled forward
    (so it won't hot-loop every tick) but no doomed run is queued."""
    suite = _suite(db_session, target=None)
    sched = _schedule(db_session, suite, next_run_at=NOW - timedelta(minutes=1))

    summary = tasks._dispatch_due_schedules(db_session, now=NOW)

    assert summary["skipped_target"] == 1 and summary["dispatched"] == 0
    assert _runs(db_session, suite.id) == []
    assert stub_run_dispatch == []
    db_session.refresh(sched)
    assert sched.next_run_at > NOW
    assert sched.last_run_at == NOW


def test_no_backfill_fires_once_for_long_gap(db_session: Any, stub_run_dispatch: list[str]) -> None:
    """An hourly schedule whose next_run_at is hours stale fires exactly once and
    advances to the next *future* hour — not one run per missed slot."""
    suite = _suite(db_session, target={"table": "ORDERS"})
    sched = _schedule(db_session, suite, cron="0 * * * *", next_run_at=NOW - timedelta(hours=5))

    summary = tasks._dispatch_due_schedules(db_session, now=NOW)

    assert summary["dispatched"] == 1
    assert (
        db_session.scalar(select(func.count()).select_from(Run).where(Run.suite_id == suite.id))
        == 1
    )
    db_session.refresh(sched)
    assert sched.next_run_at == datetime(2026, 6, 15, 13, 0, tzinfo=UTC)  # next future hour


def test_impossible_cron_disables_schedule_without_crashing(
    db_session: Any, stub_run_dispatch: list[str]
) -> None:
    """A schedule whose stored cron can never fire (Feb 30 — reachable only via a
    direct DB write that bypassed create-time validation) must disable itself, not
    crash the whole dispatch tick and hot-loop every minute."""
    suite = _suite(db_session, target={"table": "ORDERS"})
    sched = _schedule(db_session, suite, cron="0 0 30 2 *", next_run_at=NOW - timedelta(minutes=1))

    summary = tasks._dispatch_due_schedules(db_session, now=NOW)  # must not raise

    assert summary["disabled"] == 1 and summary["dispatched"] == 0
    assert _runs(db_session, suite.id) == []
    assert stub_run_dispatch == []
    db_session.refresh(sched)
    assert sched.enabled is False  # taken out of the due set so it can't hot-loop


def test_broker_failure_marks_run_failed(db_session: Any, monkeypatch: Any) -> None:
    """If the broker is unreachable at dispatch, the queued run is driven to
    `failed` (never left stuck queued) and the outcome is counted."""
    from backend.app.services import run_dispatch

    def _boom(_run_id: object) -> str:
        raise RuntimeError("broker down")

    monkeypatch.setattr(run_dispatch, "dispatch_run", _boom)

    suite = _suite(db_session, target={"table": "ORDERS"})
    _schedule(db_session, suite, next_run_at=NOW - timedelta(minutes=1))

    summary = tasks._dispatch_due_schedules(db_session, now=NOW)

    assert summary["dispatch_failed"] == 1
    runs = _runs(db_session, suite.id)
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert runs[0].finished_at is not None
