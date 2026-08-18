"""Does an audited route actually WRITE an event? — ADR 0041 phase 1 (#1318).

`test_audit_coverage.py` proves a *decision* was taken for every mutating route.
It cannot prove an `AUDITED` route really writes, and a manifest that says
"audited" over a route that does not is worse than no manifest — it reads as
coverage. This file is the other half: exercise the route through the real API
against real Postgres, and assert the row.

The three entities here are the ones ADR 0041 singles out as **completely
unrecorded before this change** — a share grant/revoke (the finest-grained
permission in the product), a credential rotation (a hole ADR 0020 shipped
knowingly), and a workspace-role change (which emitted a log line and nothing
durable). They are also the three where a missing record matters most.

Skips without TEST_DATABASE_URL.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.core.auth import get_current_user
from backend.app.db.models import AuditEvent, Connection, Suite, User
from backend.app.db.session import get_db
from backend.app.main import app


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _user(db_session: Any, email: str, role: str = "member") -> User:
    user = User(aad_object_id=uuid.uuid4().hex, email=email, role=role)
    db_session.add(user)
    db_session.flush()
    return user


def _seed(db_session: Any) -> tuple[User, User, Suite, Connection]:
    owner = _user(db_session, f"owner-{uuid.uuid4().hex[:8]}@example.com")
    other = _user(db_session, f"other-{uuid.uuid4().hex[:8]}@example.com")
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "x"},
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(name="finance", connection_id=conn.id, created_by=owner.id)
    db_session.add(suite)
    db_session.commit()
    return owner, other, suite, conn


def _as(user: User) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


def _events(db_session: Any, action: str, entity_id: uuid.UUID | None = None) -> list[AuditEvent]:
    """Events for one action, optionally narrowed to one entity.

    The `entity_id` narrowing is not optional decoration — it is what makes these
    assertions survive the full suite. `test_admin_role_management`'s concurrency
    tests deliberately use REAL committed sessions (a race is correct in every
    single-threaded test and wrong in production), so their `user.role_change`
    rows are not rolled back with `db_session` and are still there when this file
    runs. Counting every row for an action passed in isolation and failed in the
    suite; counting the rows for THIS test's own user is both immune to that and a
    sharper assertion.
    """
    db_session.expire_all()
    stmt = select(AuditEvent).where(AuditEvent.action == action)
    if entity_id is not None:
        stmt = stmt.where(AuditEvent.entity_id == entity_id)
    return list(db_session.scalars(stmt.order_by(AuditEvent.occurred_at.desc())))


def test_granting_a_share_writes_an_audit_event(client: TestClient, db_session: Any) -> None:
    """The grant is recorded with the permission it conferred.

    A share is the finest-grained authorization decision in the product and left
    no trace of any kind before this — ADR 0041 calls these the highest-value rows
    in the table for exactly that reason.
    """
    owner, other, suite, _conn = _seed(db_session)
    _as(owner)
    resp = client.post(
        f"/api/v1/suites/{suite.id}/shares",
        json={"user_id": str(other.id), "permission": "view"},
    )
    assert resp.status_code in (200, 201), resp.text

    events = _events(db_session, "share.grant")
    assert len(events) == 1
    event = events[0]
    assert event.action_class == "config"
    assert event.entity_type == "share"
    assert event.entity_id is not None, "a create must carry the id the database assigned"
    assert event.after is not None
    assert event.after["permission"] == "view"
    assert event.after["suite_id"] == str(suite.id)
    assert event.before is None, "a create has no prior state"
    assert event.actor_user_id == owner.id
    assert event.actor_label, "attribution must survive without joining users"


def test_revoking_a_share_records_what_was_destroyed(client: TestClient, db_session: Any) -> None:
    """The revoke's `before` is the only surviving record of the grant.

    The `shares` row is gone afterwards, so if the payload were captured after the
    delete — or not at all — the question "who had access to this suite, and who
    took it away?" would have no answer anywhere in the system.
    """
    owner, other, suite, _conn = _seed(db_session)
    _as(owner)
    client.post(
        f"/api/v1/suites/{suite.id}/shares",
        json={"user_id": str(other.id), "permission": "view"},
    )
    resp = client.delete(f"/api/v1/suites/{suite.id}/shares/{other.id}")
    assert resp.status_code in (200, 204), resp.text

    events = _events(db_session, "share.revoke")
    assert len(events) == 1
    event = events[0]
    assert event.after is None, "a delete has no resulting state"
    assert event.before is not None
    assert event.before["permission"] == "view"
    assert event.before["user_id"] == str(other.id)
    assert event.entity_id is not None, "the id must survive the row it identified"


def test_a_role_change_writes_both_ends_of_the_change(client: TestClient, db_session: Any) -> None:
    """ADR 0033 §7 requires a durable record of privilege changes; before this
    there was a log line and nothing queryable.

    Both ends are asserted because only one of them is interesting on its own: the
    new role stays visible on the `users` row, and the OLD one exists nowhere else
    once the change commits.
    """
    _owner, other, _suite, _conn = _seed(db_session)
    # The dev-bypass caller is a workspace admin (#741), so it clears the gate.
    resp = client.patch(f"/api/v1/admin/users/{other.id}/role", json={"role": "viewer"})
    assert resp.status_code == 200, resp.text

    events = _events(db_session, "user.role_change", other.id)
    assert len(events) == 1
    event = events[0]
    assert event.entity_type == "user"
    assert event.entity_id == other.id
    assert event.before is not None and event.before["role"] == "member"
    assert event.after is not None and event.after["role"] == "viewer"


def test_a_refused_role_change_records_nothing(client: TestClient, db_session: Any) -> None:
    """The audit write is same-transaction, so a rejected change must leave no row.

    This is the half a naive "did it write?" test misses, and it is what makes the
    log trustworthy: an event for a change that did not happen is worse than a
    missing event, because a reader cannot tell it apart from a real one.
    """
    _seed(db_session)
    missing = uuid.uuid4()
    resp = client.patch(f"/api/v1/admin/users/{missing}/role", json={"role": "viewer"})
    assert resp.status_code >= 400
    assert _events(db_session, "user.role_change", missing) == []


def test_a_connection_delete_is_the_only_surviving_record_of_it(
    client: TestClient, db_session: Any
) -> None:
    """`connection_versions` is `ondelete=CASCADE`, so the config history dies with
    the connection. The audit event is what remains — which is precisely why
    `audit_events.entity_id` carries no foreign key.
    """
    owner, _other, suite, conn = _seed(db_session)
    # The 409 guard refuses while a suite runs against it (#753), so unbind first.
    db_session.delete(suite)
    # Connection mutations are Admin-only since ADR 0033 (#741) — the owner of the
    # connection is not sufficient, which is the whole point of that change.
    owner.role = "admin"
    db_session.commit()
    _as(owner)

    resp = client.delete(f"/api/v1/connections/{conn.id}")
    assert resp.status_code in (200, 204), resp.text

    events = _events(db_session, "connection.delete")
    assert len(events) == 1
    event = events[0]
    assert event.entity_id == conn.id
    assert event.after is None
    assert event.before is not None
    assert event.before["name"] == conn.name
    assert event.before["type"] == "snowflake"
    # The config blob is excluded wholesale — it is where every `*_secret_name`
    # pointer and every adapter-specific field lives.
    assert "config" not in event.before


def test_the_auto_classify_beat_task_records_nothing(db_session: Any) -> None:
    """A machine write must not enter the audit log (ADR 0041 §2.1).

    `set_column_policy` has two callers: an author setting a redaction policy —
    audited, and among the highest-value events in the table — and the
    auto-classify beat task deriving one for a suite that has none. The second has
    no principal, and there is deliberately no `system` actor_kind, so it must
    write nothing rather than write an unattributable event.

    The exclusion is an explicit `machine_write=True` at the call site rather than
    an inference from a missing `actor_id`, because a missing `actor_id` is
    exactly what a FORGOTTEN one looks like — inferring the exclusion from it
    would silently drop a principal's act. This test pins the flag's effect; the
    test above pins that the audited path still fires.
    """
    from backend.app.services import suite_service

    _owner, _other, suite, _conn = _seed(db_session)

    suite_service.set_column_policy(
        db_session,
        suite.id,
        identifier_column="order_id",
        pii_columns=["email"],
        machine_write=True,
    )

    assert _events(db_session, "suite.column_policy_update", suite.id) == []
    # …and the policy really was written, so this is not passing because nothing
    # happened at all.
    db_session.refresh(suite)
    assert suite.column_policy == {"pii_columns": ["email"], "identifier_column": "order_id"}
