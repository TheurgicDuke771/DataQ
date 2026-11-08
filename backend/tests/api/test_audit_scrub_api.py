"""The audited scrub over HTTP: a version redaction scrubs the audit log too, and an admin can scrub
one event (ADR 0041 §9, 2026-11-08 amendment; #2547).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from backend.app.db.models import AuditEvent, Connection, User
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import audit_chain

_LITERAL = "PASTED_LITERAL"


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    # These tests assert the chain verifies, so they start from an empty one: rows another test
    # committed to the shared database are cleared inside this test's rolled-back transaction
    # (#2623).
    db_session.execute(text("DELETE FROM audit_events"))
    db_session.execute(text("UPDATE audit_chain_state SET head_hash = NULL, head_event_id = NULL"))
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _check_with_history(client: TestClient, db_session: Any) -> tuple[str, str]:
    """v1 holds a pasted literal, v2 edits it out (v2 is current)."""
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"o-{uuid.uuid4().hex[:8]}@example.com")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "ab12345.eu-west-1"},
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.commit()
    sid = client.post(
        "/api/v1/suites", json={"name": "finance", "connection_id": str(conn.id)}
    ).json()["id"]
    cid = client.post(
        f"/api/v1/suites/{sid}/checks",
        json={
            "name": "status in set",
            "expectation_type": "expect_column_values_to_be_in_set",
            "config": {"column": "status", "value_set": [_LITERAL]},
        },
    ).json()["id"]
    client.patch(
        f"/api/v1/suites/{sid}/checks/{cid}",
        json={"config": {"column": "status", "value_set": ["open", "closed"]}},
    )
    return sid, cid


def _check_events(db_session: Any, cid: str) -> list[AuditEvent]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(AuditEvent)
            .where(AuditEvent.entity_type == "check", AuditEvent.entity_id == uuid.UUID(cid))
            .order_by(AuditEvent.occurred_at)
        )
    )


def test_redacting_a_version_scrubs_the_events_that_carried_it(
    client: TestClient, db_session: Any
) -> None:
    sid, cid = _check_with_history(client, db_session)
    assert _LITERAL in json.dumps([e.after for e in _check_events(db_session, cid)])

    resp = client.delete(f"/api/v1/suites/{sid}/checks/{cid}/versions/1")
    assert resp.status_code == 200

    events = _check_events(db_session, cid)
    payloads = json.dumps([(e.before, e.after) for e in events])
    assert _LITERAL not in payloads
    update = next(e for e in events if e.action == "check.update")
    assert update.before is not None and update.after is not None
    assert update.before["config"] == {"column": "status"}
    # The edit that replaced the literal keeps what it replaced it with.
    assert update.after["config"] == {"column": "status", "value_set": ["open", "closed"]}
    assert {e.action for e in events if e.scrubbed_at is not None} == {
        "check.create",
        "check.update",
    }

    scrubs = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == audit_chain.SCRUB_ACTION)
    ).all()
    assert {s.after["trigger"] for s in scrubs} == {"version_redaction"}
    assert {s.after["reason"] for s in scrubs} == {"check version 1 redacted"}

    verify = client.get("/api/v1/admin/audit-events/verify").json()
    assert verify["status"] == "ok" and verify["scrubbed_count"] == 2

    listed = client.get(
        "/api/v1/admin/audit-events", params={"entity_type": "check", "entity_id": cid}
    ).json()["events"]
    assert all(
        (e["scrubbed_at"] is not None) == (e["action"] in {"check.create", "check.update"})
        for e in listed
    )


def test_an_admin_scrubs_one_event(client: TestClient, db_session: Any) -> None:
    _sid, cid = _check_with_history(client, db_session)
    update = next(e for e in _check_events(db_session, cid) if e.action == "check.update")

    resp = client.post(
        f"/api/v1/admin/audit-events/{update.id}/scrub", json={"reason": "a token in the SQL"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["scrubbed_at"] is not None
    assert body["before"]["config"] == {"column": "status"}
    assert body["after"]["config"] == {"column": "status"}

    again = client.post(f"/api/v1/admin/audit-events/{update.id}/scrub", json={"reason": "r"})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "audit_event_not_scrubbable"

    verify = client.get("/api/v1/admin/audit-events/verify").json()
    assert verify["status"] == "ok" and verify["scrubbed_count"] == 1


def test_the_scrub_route_refuses_bad_input(client: TestClient, db_session: Any) -> None:
    _sid, cid = _check_with_history(client, db_session)
    event_id = _check_events(db_session, cid)[0].id

    blank = client.post(f"/api/v1/admin/audit-events/{event_id}/scrub", json={"reason": ""})
    assert blank.status_code == 422
    unknown = client.post(f"/api/v1/admin/audit-events/{uuid.uuid4()}/scrub", json={"reason": "r"})
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "audit_event_not_found"


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_only_a_workspace_admin_scrubs(
    client: TestClient, db_session: Any, as_role: Any, role: str
) -> None:
    _sid, cid = _check_with_history(client, db_session)
    event_id = _check_events(db_session, cid)[0].id
    _user, headers = as_role(role)

    resp = client.post(
        f"/api/v1/admin/audit-events/{event_id}/scrub", json={"reason": "r"}, headers=headers
    )
    assert resp.status_code == 403
    assert _check_events(db_session, cid)[0].scrubbed_at is None
