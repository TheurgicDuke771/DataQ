"""A contended row must never hang the poll — it took prod down (#854).

#837 added a row lock to the poll's health bookkeeping (so two overlapping sweeps can't
both fire the same alert). A Postgres lock waits **forever** by default, and that was
enough to take production down: one contended `connections` row hung the poll task, the
hung task wedged the worker's prefork pool, and the pool being wedged silently stopped
**every** periodic task — orchestration polling, scheduled-suite dispatch, gap recovery,
the sample purge.

Nothing looked wrong. The container reported Healthy, Celery logged "ready", the beat
logged "Starting…", and zero exceptions were raised. Only the database told the truth:
`last_polled_at` stayed NULL while the app insisted it was fine.

The lesson is the size of the blast radius, not the lock: the poll's *bookkeeping* is
best-effort, but it was allowed to block a **shared** beat worker indefinitely. These
tests hold a real lock from a second connection and assert the poll path returns quickly
instead of blocking — they FAIL (hang) against the pre-#854 code.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from backend.app.db.models import Connection, User
from backend.app.services import orchestration_service

# Generous enough that a slow CI box isn't flaky, tight enough that a genuine hang (which
# is unbounded) can't sneak past.
_MUST_RETURN_WITHIN = 25.0


def _committed_connection(conn_type: str) -> uuid.UUID:
    """A connection row COMMITTED for real, so a *second* session can see and lock it.

    Deliberately not the `db_session` fixture: that wraps the test in a transaction it
    rolls back, so its rows are invisible to other sessions — and `SELECT … FOR UPDATE`
    on a row nobody else can see locks NOTHING. The first draft of this test did exactly
    that and passed against the bug. A lock test whose lock isn't real proves nothing.
    """
    from backend.app.db.session import SessionLocal

    session = SessionLocal()
    try:
        user = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@ex.io")
        session.add(user)
        session.flush()
        conn = Connection(
            name=f"{conn_type}-{uuid.uuid4().hex[:8]}",
            type=conn_type,
            env="dev",
            config={"base_url": "http://x", "project_name": "p", "factory_name": "f"},
            secret_ref="kv",
            created_by=user.id,
        )
        session.add(conn)
        session.commit()
        return conn.id
    finally:
        session.close()


def _delete_connection(connection_id: uuid.UUID) -> None:
    from backend.app.db.session import SessionLocal

    session = SessionLocal()
    try:
        session.execute(text("DELETE FROM connections WHERE id = :i"), {"i": str(connection_id)})
        session.commit()
    finally:
        session.close()


def _unused(db: Any, conn_type: str = "airflow") -> Connection:
    # `env` is CHECK-constrained to dev/qa/uat/prod and an orchestrator is unique per
    # (type, env) — so each test uses its own type rather than a random env.
    user = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@ex.io")
    db.add(user)
    db.flush()
    conn = Connection(
        name=f"{conn_type}-{uuid.uuid4().hex[:8]}",
        type=conn_type,
        env="dev",
        config={"base_url": "http://x", "project_name": "p", "factory_name": "f"},
        secret_ref="kv",
        created_by=user.id,
    )
    db.add(conn)
    db.commit()
    return conn


def _run_with_deadline(fn: Any) -> bool:
    """Run ``fn`` on a thread; True if it finished, False if it is still blocked.

    A hang cannot be caught with `pytest.raises` — the point of the bug is that it never
    returns at all — so the assertion has to be a deadline. Any exception the thread raises
    is re-raised here, so a silently-erroring call can't masquerade as "it returned".
    """
    done = threading.Event()
    error: list[BaseException] = []

    def target() -> None:
        try:
            fn()
        except BaseException as exc:  # re-raised on the main thread below
            error.append(exc)
        finally:
            done.set()

    threading.Thread(target=target, daemon=True).start()
    finished = done.wait(timeout=_MUST_RETURN_WITHIN)
    if error:
        raise error[0]
    return finished


@pytest.fixture
def held_lock(request: Any, db_session: Any) -> Any:
    """A REAL `FOR UPDATE` lock, held by a second session on a committed row — the prod
    condition, and the thing whose absence made the first draft of this test worthless."""
    from backend.app.db.session import SessionLocal

    connection_id = _committed_connection(getattr(request, "param", "airflow"))
    holder = SessionLocal()
    locked = holder.execute(
        text("SELECT id FROM connections WHERE id = :i FOR UPDATE"), {"i": str(connection_id)}
    ).first()
    assert locked is not None, "the lock holder found no row — the lock would be a no-op"
    try:
        yield connection_id
    finally:
        holder.rollback()
        holder.close()
        _delete_connection(connection_id)


def test_record_poll_failure_does_not_hang_on_a_contended_row(
    db_session: Any, held_lock: uuid.UUID
) -> None:
    """The exact prod wedge. Pre-#854 this blocks forever and the test times out."""
    from backend.app.db.session import SessionLocal

    def call() -> None:
        session = SessionLocal()
        try:
            orchestration_service.record_poll_failure(
                session, connection_id=held_lock, exc=RuntimeError("boom")
            )
        finally:
            session.close()

    assert _run_with_deadline(call), (
        "record_poll_failure blocked on a contended row — this is what wedged the worker "
        "pool and stopped every periodic task in prod"
    )


@pytest.mark.parametrize("held_lock", ["adf"], indirect=True)
def test_record_poll_success_does_not_hang_on_a_contended_row(
    db_session: Any, held_lock: uuid.UUID
) -> None:
    from backend.app.db.session import SessionLocal

    def call() -> None:
        session = SessionLocal()
        try:
            conn = session.get(Connection, held_lock)
            assert conn is not None, "the committed connection row is missing"
            orchestration_service.record_poll_success(session, connection=conn)
        finally:
            session.close()

    assert _run_with_deadline(call), "record_poll_success blocked on a contended row"


@pytest.mark.parametrize("held_lock", ["dbt"], indirect=True)
def test_the_sweep_survives_a_contended_row(
    db_session: Any, held_lock: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The property that actually matters: a contended row degrades the *bookkeeping*, not
    the sweep. The poll must still finish, so the beat keeps running and the next task
    gets its turn."""
    from datetime import UTC, datetime, timedelta

    from backend.app.db.session import SessionLocal
    from backend.app.worker import tasks

    class _Store:
        def get(self, name: str) -> str:
            return "secret"

        def set(self, name: str, value: str) -> None: ...

        def delete(self, name: str) -> None: ...

    class _Provider:
        provider = "airflow"
        resource_config_key = "base_url"

        def list_recent_runs(self, config: Any, secret: str, since: Any) -> Any:
            raise RuntimeError("orchestrator unreachable")

    monkeypatch.setattr(tasks, "get_orchestration_provider", lambda _type: _Provider())
    summary: dict[str, int] = {}

    def call() -> None:
        session = SessionLocal()
        try:
            summary.update(
                tasks._poll_orchestration_runs(
                    session,
                    secret_store=_Store(),
                    lookback=timedelta(minutes=15),
                    now=datetime.now(UTC),
                )
            )
        finally:
            session.close()

    assert _run_with_deadline(call), (
        "the poll sweep hung on a contended row — a wedged sweep takes the whole beat "
        "down with it (#854)"
    )
    assert summary, "the sweep returned no summary — it did not complete"
