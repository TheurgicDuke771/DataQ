"""Exactly-once dispatch under two concurrent dispatchers (#1999) — REAL commits.

The suite's `db_session` shares one transaction, so it cannot show what a second
connection sees; this runs two real sessions against a scratch database.
"""

import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.models import Connection, Run, Schedule, Suite, User
from backend.app.services import schedule_dispatch
from backend.tests.support.scratch_db import scratch_engine

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
_PROBE_DB = "dataq_schedule_dispatch_probe"
_WAIT_S = 10


@pytest.fixture(scope="module")
def probe_engine() -> Any:
    with scratch_engine(_PROBE_DB) as engine:
        yield engine


@pytest.fixture
def seeded(probe_engine: Any) -> Any:
    maker = sessionmaker(bind=probe_engine)
    with maker() as setup:
        owner = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:6]}@x.test")
        setup.add(owner)
        setup.flush()
        conn = Connection(
            name="sf", type="snowflake", env="dev", config={"account": "a"}, created_by=owner.id
        )
        setup.add(conn)
        setup.flush()
        suite = Suite(
            name="s", connection_id=conn.id, created_by=owner.id, target={"table": "ORDERS"}
        )
        setup.add(suite)
        setup.flush()
        schedules = [
            Schedule(
                suite_id=suite.id,
                cron="0 * * * *",
                timezone="UTC",
                next_run_at=NOW - timedelta(minutes=10 - i),
            )
            for i in range(6)
        ]
        setup.add_all(schedules)
        setup.commit()
        ordered = [s.id for s in schedules]  # oldest next_run_at first
    yield maker, ordered
    with maker() as cleanup:
        for model in (Run, Schedule, Suite, Connection, User):
            cleanup.execute(delete(model))
        cleanup.commit()


def test_concurrent_dispatchers_never_fire_a_schedule_twice(
    seeded: Any, stub_run_dispatch: list[str]
) -> None:
    """A claims the 3 oldest and holds their locks uncommitted; B runs to completion
    meanwhile. B must take exactly the other 3 — which proves it ran while A's locks
    were held — and each schedule ends with one run.
    """
    maker, ordered = seeded
    a_holds_locks = threading.Event()
    release_a = threading.Event()
    a_committed = threading.Event()
    results: dict[str, Any] = {}

    def run_a() -> None:
        session: Session = maker()
        real_commit = session.commit
        paused = [False]

        def commit_after_b() -> None:
            if not paused[0]:
                paused[0] = True
                a_holds_locks.set()
                release_a.wait(timeout=_WAIT_S)
                a_committed.set()
            real_commit()

        session.commit = commit_after_b  # type: ignore[method-assign]
        try:
            results["a"] = schedule_dispatch.dispatch_due_schedules(session, now=NOW, batch_size=3)
        except Exception as exc:  # pragma: no cover - surfaced by the assertions
            results["a_error"] = exc
        finally:
            session.close()

    def run_b() -> None:
        session: Session = maker()
        try:
            results["b"] = schedule_dispatch.dispatch_due_schedules(session, now=NOW)
            results["b_saw_a_uncommitted"] = not a_committed.is_set()
        except Exception as exc:  # pragma: no cover - surfaced by the assertions
            results["b_error"] = exc
        finally:
            session.close()

    thread_a = threading.Thread(target=run_a)
    thread_b = threading.Thread(target=run_b)
    thread_a.start()
    try:
        assert a_holds_locks.wait(timeout=_WAIT_S), "dispatcher A never reached its commit"
        thread_b.start()
        thread_b.join(timeout=_WAIT_S)
        b_finished = not thread_b.is_alive()
    finally:
        release_a.set()
        thread_a.join(timeout=_WAIT_S)
        if thread_b.ident is not None:
            thread_b.join(timeout=_WAIT_S)

    assert "a_error" not in results and "b_error" not in results
    with maker() as reader:
        fired = [r.triggered_by for r in reader.scalars(select(Run))]
    # The headline: every schedule fired exactly once across both dispatchers.
    assert sorted(fired) == sorted(f"schedule:{sid}" for sid in ordered)
    # And it held BECAUSE B ran beside A's uncommitted claim and skipped it — not
    # because the two happened to run one after the other.
    assert b_finished, "dispatcher B blocked on A's claimed rows instead of skipping them"
    assert results["b_saw_a_uncommitted"] is True
    assert results["a"]["due"] == 3 and results["b"]["due"] == 3


def test_a_rolled_back_batch_is_not_published_and_fires_next_tick(
    seeded: Any, stub_run_dispatch: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Publishing follows the commit: a batch whose commit fails reaches no broker,
    and its schedules stay due for the next tick rather than being lost.
    """
    maker, _ordered = seeded
    session: Session = maker()

    def boom() -> None:
        raise RuntimeError("connection lost at commit")

    monkeypatch.setattr(session, "commit", boom)
    with pytest.raises(RuntimeError):
        schedule_dispatch.dispatch_due_schedules(session, now=NOW)
    session.close()

    assert stub_run_dispatch == []
    with maker() as retry:
        summary = schedule_dispatch.dispatch_due_schedules(retry, now=NOW)
    assert summary["dispatched"] == 6
    with maker() as reader:
        assert len(reader.scalars(select(Run)).all()) == 6
