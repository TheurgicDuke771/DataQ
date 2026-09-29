"""Two accepts of one suggestion at once create ONE check (real sessions, real commits).

The shared-transaction `db_session` fixture can't show this: the race lives in the row lock
being released when `create_check` commits, so it needs two connections and committed data.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from backend.app.db.models import Check, CheckSuggestion, Connection, Suite, User
from backend.app.services import check_service
from backend.app.services import suggestion_service as svc


@pytest.fixture
def committed(_db_engine: Any) -> Any:
    with Session(_db_engine) as s:
        admin = User(
            aad_object_id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex[:8]}@x.io", role="admin"
        )
        s.add(admin)
        s.flush()
        conn = Connection(
            name=f"pg-{uuid.uuid4().hex[:6]}",
            type="postgres",
            env="dev",
            config={"host": "h", "database": "shop", "user": "r"},
            secret_ref="kv",
            created_by=admin.id,
        )
        s.add(conn)
        s.flush()
        suite = Suite(
            name="race", connection_id=conn.id, created_by=admin.id, target={"table": "t"}
        )
        s.add(suite)
        s.flush()
        svc.propose(
            s,
            suite,
            source="profile",
            name="order_id is never null",
            expectation_type="expect_column_values_to_not_be_null",
            config={"column": "order_id"},
            rationale=None,
        )
        s.commit()
        suggestion_id = s.scalar(
            select(CheckSuggestion.id).where(CheckSuggestion.suite_id == suite.id)
        )
        ids = (admin.id, conn.id, suite.id, suggestion_id)
    yield ids
    admin_id, conn_id, suite_id, _ = ids
    with Session(_db_engine) as s:
        s.execute(delete(Suite).where(Suite.id == suite_id))
        s.execute(delete(Connection).where(Connection.id == conn_id))
        s.execute(delete(User).where(User.id == admin_id))
        s.commit()


def test_concurrent_accepts_create_one_check(
    _db_engine: Any, committed: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin_id, _, suite_id, suggestion_id = committed
    first_holds_lock = threading.Event()
    release_first = threading.Event()
    real_create = check_service.create_check

    def slow_create(session: Session, **kwargs: Any) -> Check:
        # Called with the row lock held and the claim made, before anything commits.
        if threading.current_thread().name == "first":
            first_holds_lock.set()
            release_first.wait(timeout=10)
        return real_create(session, **kwargs)

    monkeypatch.setattr(check_service, "create_check", slow_create)
    outcomes: dict[str, Any] = {}

    def run(name: str) -> None:
        with Session(_db_engine) as s:
            try:
                outcomes[name] = svc.accept(s, suggestion_id, user_id=admin_id).status
            except Exception as exc:
                outcomes[name] = type(exc).__name__

    first = threading.Thread(target=run, args=("first",), name="first")
    first.start()
    assert first_holds_lock.wait(timeout=10)
    second = threading.Thread(target=run, args=("second",), name="second")
    second.start()
    second.join(timeout=1)
    assert second.is_alive()  # blocked on the row lock, not racing ahead
    release_first.set()
    first.join(timeout=10)
    second.join(timeout=10)

    assert outcomes == {"first": "accepted", "second": "SuggestionDecidedError"}
    with Session(_db_engine) as s:
        count = s.scalar(select(func.count()).select_from(Check).where(Check.suite_id == suite_id))
    assert count == 1
