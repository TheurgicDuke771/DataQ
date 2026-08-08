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


def _committed_connection(engine: Any, conn_type: str) -> tuple[uuid.UUID, uuid.UUID]:
    """A connection (+ its owning user) row COMMITTED for real, so a *second* session
    can see and lock it. Returns (connection_id, user_id).

    Deliberately not the `db_session` fixture: that wraps the test in a transaction it
    rolls back, so its rows are invisible to other sessions — and `SELECT … FOR UPDATE`
    on a row nobody else can see locks NOTHING. The first draft of this test did exactly
    that and passed against the bug. A lock test whose lock isn't real proves nothing.

    Bound to `engine` (the conftest `_db_engine` fixture, built straight from
    `TEST_DATABASE_URL`) — deliberately NOT the app's `SessionLocal` (#1133). Even
    though conftest now points `DATABASE_URL` at the test DB by default (#1130),
    `SessionLocal` still resolves whatever `DATABASE_URL` the environment happens to
    carry — including a deliberately different one under the opt-in E2E case — and
    this setup/holder row must always land in the TEST database, never wherever that
    happens to be.
    """
    from sqlalchemy.orm import Session as SASession

    session = SASession(bind=engine)
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
        return conn.id, user.id
    finally:
        session.close()


def _delete_connection_and_user(engine: Any, connection_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """Teardown for `_committed_connection` — removes BOTH rows it created (#1133).

    The prior version only deleted the `Connection`, leaving the `User` behind on
    every run (the stray `u-<hex>@ex.io` accumulation #1130 reported in the dev DB).
    """
    from sqlalchemy.orm import Session as SASession

    session = SASession(bind=engine)
    try:
        session.execute(text("DELETE FROM connections WHERE id = :i"), {"i": str(connection_id)})
        session.execute(text("DELETE FROM users WHERE id = :i"), {"i": str(user_id)})
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
        except Exception as exc:  # re-raised on the main thread below
            error.append(exc)
        except BaseException as exc:
            # SystemExit / pytest's Failed deliberately subclass BaseException to
            # escape except-Exception blocks — letting one vanish with the thread
            # would make a blown-up fn read as "it returned" (the masquerade this
            # helper exists to prevent). Record for the main thread, then
            # RE-RAISE in place (CodeQL py/catch-base-exception: never swallow).
            error.append(exc)
            raise
        finally:
            done.set()

    threading.Thread(target=target, daemon=True).start()
    finished = done.wait(timeout=_MUST_RETURN_WITHIN)
    if error:
        raise error[0]
    return finished


@pytest.fixture
def held_lock(request: Any, db_session: Any, _db_engine: Any) -> Any:
    """A REAL `FOR UPDATE` lock, held by a second session on a committed row — the prod
    condition, and the thing whose absence made the first draft of this test worthless.

    Both the committed row and the holder session are bound to `_db_engine` (the
    conftest fixture built from `TEST_DATABASE_URL`), not `SessionLocal` — see
    `_committed_connection` (#1133).
    """
    from sqlalchemy.orm import Session as SASession

    connection_id, user_id = _committed_connection(_db_engine, getattr(request, "param", "airflow"))
    holder = SASession(bind=_db_engine)
    locked = holder.execute(
        text("SELECT id FROM connections WHERE id = :i FOR UPDATE"), {"i": str(connection_id)}
    ).first()
    assert locked is not None, "the lock holder found no row — the lock would be a no-op"
    try:
        yield connection_id
    finally:
        holder.rollback()
        holder.close()
        _delete_connection_and_user(_db_engine, connection_id, user_id)


def test_record_poll_failure_does_not_hang_on_a_contended_row(
    db_session: Any, held_lock: uuid.UUID
) -> None:
    """The exact prod wedge. Pre-#854 this blocks forever and the test times out."""
    from backend.app.db.session import SessionLocal

    def call() -> None:
        session = SessionLocal()
        try:
            # Visibility check (review finding, #855 vacuous-lock shape): if
            # SessionLocal's DATABASE_URL ever diverges from the held_lock row's
            # TEST_DATABASE_URL (the opt-in E2E case), this query returns None,
            # `orchestration_service._lock_connection`'s "row not found" branch takes
            # over, and the assert below would pass with NO real lock ever contended
            # — the exact "first draft passed against the bug" trap `_committed_
            # connection`'s docstring warns about, one layer up. Mirrors the sibling
            # `test_record_poll_success_...` below, which already had this guard.
            conn = session.get(Connection, held_lock)
            assert conn is not None, "the committed connection row is missing"
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
            # Visibility check (review finding, #855 vacuous-lock shape) — same
            # reasoning as test_record_poll_failure_... above: `_poll_orchestration_
            # runs` queries `connections` internally by its own criteria, so nothing
            # else here would fail loudly if SessionLocal's database ever diverged
            # from held_lock's and the row were simply invisible to it.
            conn = session.get(Connection, held_lock)
            assert conn is not None, "the committed connection row is missing"
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


def test_a_real_db_fault_is_not_mislabelled_as_lock_contention(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`OperationalError` also covers a dropped connection / server restart. Swallowing
    those as "the row was busy" would report a genuine DB outage as routine contention —
    and the whole lesson of #854 is what an invisible failure costs (#855 review)."""
    from sqlalchemy.exc import OperationalError

    class _Dead:
        pgcode = "57P01"  # admin_shutdown — NOT lock_not_available

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise OperationalError("SELECT 1", {}, _Dead())  # type: ignore[arg-type]  # orig is duck-typed

    monkeypatch.setattr(
        orchestration_service, "_lock_connection", orchestration_service._lock_connection
    )
    session = db_session
    monkeypatch.setattr(type(session), "get", lambda *a, **k: _boom())

    with pytest.raises(OperationalError):
        orchestration_service.record_poll_failure(
            session, connection_id=uuid.uuid4(), exc=RuntimeError("x")
        )


def test_the_engine_bounds_every_lock_wait(db_session: Any) -> None:
    """The class-level guard (#855 review): the timeout lives on the ENGINE, so NO
    statement anywhere — not merely these two functions — can block on a lock forever.

    The original defect was never "these two functions lock a row"; it was that anything
    sharing the beat could block indefinitely and take every periodic task with it. A
    per-callsite guard would leave that property intact for the next `with_for_update`
    someone adds. Asserted against a REAL connection (`SHOW lock_timeout`), not by
    introspecting SQLAlchemy's kwargs — what matters is what Postgres actually enforces.
    """
    from backend.app.db.session import _LOCK_TIMEOUT_MS, SessionLocal

    session = SessionLocal()
    try:
        setting = session.execute(text("SHOW lock_timeout")).scalar_one()
    finally:
        session.close()

    assert setting not in ("0", "0ms"), "lock_timeout is unset — a lock can block forever"
    assert str(_LOCK_TIMEOUT_MS) in str(setting) or setting.endswith("s")


def test_the_engine_bounds_the_initial_connect_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """#1102: `lock_timeout` only bounds a statement waiting on a contended row AFTER a
    connection is established — it says nothing about the initial TCP connect. An
    unreachable DB (network partition, not a locked row) would otherwise block every
    `get_session()` caller — including the #1052 staleness loop's graceful-shutdown await
    — for however long the OS/driver default connect timeout allows (can be minutes).

    Asserted by capturing the `connect_args` that `_build_engine` actually hands to
    `create_engine`, deliberately NOT by dialing an unreachable host: that would make this
    test itself slow/flaky by the exact amount we're trying to bound, and CI has no
    deterministic way to guarantee a host is unreachable. What matters here is that the
    config reaches the engine; that psycopg2 honors `connect_timeout` is the driver's own
    documented contract, not ours to re-verify.
    """
    from backend.app.db import session as session_module

    captured: dict[str, Any] = {}

    def _fake_create_engine(url: Any, **kwargs: Any) -> str:
        captured.update(kwargs)
        return "not-a-real-engine"

    monkeypatch.setattr(session_module, "create_engine", _fake_create_engine)

    session_module._build_engine()

    connect_args = captured.get("connect_args")
    assert connect_args is not None, "_build_engine no longer passes connect_args at all"
    assert connect_args.get("connect_timeout") == session_module._CONNECT_TIMEOUT_SECONDS
    # The existing lock_timeout posture must survive alongside the new option — this
    # isn't a replacement, it's an addition at a different layer (statement vs. connect).
    assert f"lock_timeout={session_module._LOCK_TIMEOUT_MS}" in connect_args.get("options", "")
