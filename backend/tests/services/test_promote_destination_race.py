"""A promote and a clear of the same legacy webhook at once (real sessions, real commits).

The clear deletes the secret it takes off the row, after its commit. If it could read the
ref while a promote was moving it, it would delete the secret the new channel now owns.
The shared-transaction `db_session` fixture can't show this: it needs two connections.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.db.models import (
    AuditEvent,
    Connection,
    NotificationChannel,
    Suite,
    SuiteNotification,
    SuiteNotificationChannel,
    User,
)
from backend.app.services import audit_service, channel_service, notification_service
from backend.tests.support.fake_secret_store import FakeSecretStore

_REF = "suite-notif-race"


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
        s.add(SuiteNotification(suite_id=suite.id, webhook_secret_ref=_REF))
        s.commit()
        ids = (admin.id, conn.id, suite.id)
    yield ids
    admin_id, conn_id, suite_id = ids
    with Session(_db_engine) as s:
        channel_ids = list(
            s.scalars(
                select(SuiteNotificationChannel.channel_id).where(
                    SuiteNotificationChannel.suite_id == suite_id
                )
            )
        )
        s.execute(delete(Suite).where(Suite.id == suite_id))
        s.execute(delete(NotificationChannel).where(NotificationChannel.id.in_(channel_ids)))
        s.execute(delete(Connection).where(Connection.id == conn_id))
        s.execute(delete(AuditEvent).where(AuditEvent.actor_user_id == admin_id))
        s.execute(delete(User).where(User.id == admin_id))
        s.commit()


def test_a_clear_racing_a_promote_cannot_delete_the_moved_secret(
    _db_engine: Any, committed: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin_id, _, suite_id = committed
    store = FakeSecretStore({_REF: "https://contoso.webhook.office.com/x"})
    promote_holds_lock = threading.Event()
    release_promote = threading.Event()
    real_record = audit_service.record_entity_change

    def slow_record(session: Session, **kwargs: Any) -> Any:
        # The promote's first audit write: the row lock is held, nothing has committed.
        if threading.current_thread().name == "promote":
            promote_holds_lock.set()
            release_promote.wait(timeout=10)
        return real_record(session, **kwargs)

    monkeypatch.setattr(audit_service, "record_entity_change", slow_record)
    errors: list[Exception] = []

    def promote() -> None:
        with Session(_db_engine) as s:
            try:
                channel_service.promote_suite_destination(
                    s, suite_id, destination="teams", name="Race", actor_id=admin_id
                )
            except Exception as exc:
                errors.append(exc)

    def clear() -> None:
        with Session(_db_engine) as s:
            try:
                notification_service.upsert_config(
                    s,
                    suite_id=suite_id,
                    enabled=True,
                    alert_on="warn",
                    webhook="",
                    secret_store=store,
                    actor_id=admin_id,
                )
            except Exception as exc:
                errors.append(exc)

    first = threading.Thread(target=promote, name="promote")
    first.start()
    assert promote_holds_lock.wait(timeout=10)
    second = threading.Thread(target=clear, name="clear")
    second.start()
    second.join(timeout=1)
    assert second.is_alive()  # blocked on the row lock, not reading the ref being moved
    release_promote.set()
    first.join(timeout=10)
    second.join(timeout=10)

    assert errors == []
    assert store.deleted == []
    with Session(_db_engine) as s:
        assert channel_service.resolve_channel_webhooks(
            s, suite_id, channel_type="teams", secret_store=store
        ) == [store.data[_REF]]
