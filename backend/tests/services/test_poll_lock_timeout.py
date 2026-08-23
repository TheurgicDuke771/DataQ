"""A contended row must never hang the poll — it took prod down (#854)."""

from __future__ import annotations

import threading
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from backend.app.db.models import Connection, User
from backend.app.services import orchestration_service
from backend.tests.support.fake_secret_store import FakeSecretStore

# Generous enough that a slow CI box isn't flaky, tight enough that a genuine hang (which
# is unbounded) can't sneak past.
_MUST_RETURN_WITHIN = 25.0


def _committed_connection(engine: Any, conn_type: str) -> tuple[uuid.UUID, uuid.UUID]:
    """A connection (+ its owning user) row COMMITTED for real, so a *second* session
    can see and lock it. Returns (connection_id, user_id).
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
    """Teardown for `_committed_connection` — removes BOTH rows it created (#1133)."""
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
    """Run ``fn`` on a thread; True if it finished, False if it is still blocked."""
    done = threading.Event()
    error: list[BaseException] = []

    def target() -> None:
        try:
            fn()
        except Exception as exc:  # re-raised on the main thread below
            error.append(exc)
        except BaseException as exc:
            # SystemExit / pytest's Failed deliberately subclass BaseException to escape except-
            # Exception blocks.
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
            # Visibility check (review finding, #855 vacuous-lock shape): if SessionLocal's
            # DATABASE_URL ever diverges from the held_lock row's TEST_DATABASE_URL (the opt-in E2E
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
    gets its turn.
    """
    from datetime import UTC, datetime, timedelta

    from backend.app.db.session import SessionLocal
    from backend.app.worker import tasks

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
            # Visibility check (review finding, #855 vacuous-lock shape).
            conn = session.get(Connection, held_lock)
            assert conn is not None, "the committed connection row is missing"
            summary.update(
                tasks._poll_orchestration_runs(
                    session,
                    secret_store=FakeSecretStore(default="secret"),
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
    and the whole lesson of #854 is what an invisible failure costs (#855 review).
    """
    from sqlalchemy.exc import OperationalError

    class _Dead:
        pgcode = "57P01"  # admin_shutdown — NOT lock_not_available

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise OperationalError("SELECT 1", {}, _Dead())  # type: ignore[arg-type]  # orig is duck-typed

    # (The lock helper itself now lives in `services/connection_lock.py`, shared with the #1104
    # inventory sync.
    session = db_session
    monkeypatch.setattr(type(session), "get", lambda *a, **k: _boom())

    with pytest.raises(OperationalError):
        orchestration_service.record_poll_failure(
            session, connection_id=uuid.uuid4(), exc=RuntimeError("x")
        )


def test_the_engine_bounds_every_lock_wait(db_session: Any) -> None:
    """The class-level guard (#855 review): the timeout lives on the ENGINE, so NO
    statement anywhere — not merely these two functions — can block on a lock forever.
    """
    from backend.app.db.session import _LOCK_TIMEOUT_MS, SessionLocal

    session = SessionLocal()
    try:
        setting = session.execute(text("SHOW lock_timeout")).scalar_one()
    finally:
        session.close()

    assert setting not in ("0", "0ms"), "lock_timeout is unset — a lock can block forever"
    assert str(_LOCK_TIMEOUT_MS) in str(setting) or setting.endswith("s")


def _captured_build_engine_connect_args(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Shared scaffold for the two `_build_engine` connect_args tests below: swaps in a fake
    `create_engine` that captures its kwargs instead of dialing a real (or unreachable/black-
    holed) host, then calls `_build_engine()` and hands back whatever `connect_args` it built.
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
    return dict(connect_args)


def test_the_engine_bounds_the_initial_connect_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """#1102: `lock_timeout` only bounds a statement waiting on a contended row AFTER a connection
    is established — it says nothing about the initial TCP connect.
    """
    from backend.app.db import session as session_module

    connect_args = _captured_build_engine_connect_args(monkeypatch)
    assert connect_args.get("connect_timeout") == session_module._CONNECT_TIMEOUT_SECONDS
    # The existing lock_timeout posture must survive alongside the new option — this
    # isn't a replacement, it's an addition at a different layer (statement vs. connect).
    assert f"lock_timeout={session_module._LOCK_TIMEOUT_MS}" in connect_args.get("options", "")


def test_the_engine_bounds_a_warm_pooled_connection_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#1221: `connect_timeout` (#1102, above) only bounds establishing a BRAND-NEW connection. It
    does nothing for a connection that was already open and pooled when a network partition
    happens LATER — route drops silently, no TCP RST.
    """
    from backend.app.db import session as session_module

    connect_args = _captured_build_engine_connect_args(monkeypatch)
    assert connect_args.get("keepalives") == 1, "TCP keepalives are not enabled at all"
    assert connect_args.get("keepalives_idle") == session_module._KEEPALIVES_IDLE_SECONDS
    assert connect_args.get("keepalives_interval") == session_module._KEEPALIVES_INTERVAL_SECONDS
    assert connect_args.get("keepalives_count") == session_module._KEEPALIVES_COUNT
    # The connect_timeout (#1102) and lock_timeout (#855) postures must survive alongside the new
    # keepalive options — this is an addition at a third layer (warm-pooled-connection read).
    assert connect_args.get("connect_timeout") == session_module._CONNECT_TIMEOUT_SECONDS
    assert f"lock_timeout={session_module._LOCK_TIMEOUT_MS}" in connect_args.get("options", "")
