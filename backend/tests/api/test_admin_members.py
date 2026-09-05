"""`/admin/members` — the workspace-membership write axis (ADR 0043 decision 2)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.db.models import AuditEvent, User
from backend.app.db.session import get_db
from backend.app.main import app


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _addr(prefix: str = "p") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


def _seed_user(db: Any, role: str = "member") -> User:
    row = User(id=uuid.uuid4(), aad_object_id=uuid.uuid4().hex, email=_addr(role), role=role)
    db.add(row)
    db.commit()
    return row


# ── reads ─────────────────────────────────────────────────────────────────────


def test_an_empty_list_says_enforcement_is_off(client: TestClient, db_session: Any) -> None:
    """The honesty field: an empty members list is not "nobody has access"."""
    _seed_user(db_session)
    body = client.get("/api/v1/admin/members").json()

    assert body["enforcement_active"] is False
    assert body["members"] == []
    assert body["unmanaged_user_count"] >= 1


def test_adding_a_member_reports_what_the_switch_did(client: TestClient, db_session: Any) -> None:
    _seed_user(db_session)
    email = _addr("new")

    body = client.post(
        "/api/v1/admin/members", json={"email": email, "initial_role": "viewer"}
    ).json()

    assert body["member"]["email"] == email
    assert body["member"]["initial_role"] == "viewer"
    assert body["member"]["source"] == "admin"
    assert body["member"]["status"] == "pending"
    assert body["auto_imported_count"] >= 1
    assert body["enforcement_active"] is True


def test_the_list_marks_imported_rows_for_review(client: TestClient, db_session: Any) -> None:
    existing = _seed_user(db_session)
    client.post("/api/v1/admin/members", json={"email": _addr("new")})

    body = client.get("/api/v1/admin/members").json()
    imported = [m for m in body["members"] if m["source"] == "auto_import"]

    assert body["enforcement_active"] is True
    assert existing.email in [m["email"] for m in imported]
    assert all(m["status"] == "active" for m in imported)


# ── writes ────────────────────────────────────────────────────────────────────


def test_confirming_clears_the_provisional_flag(client: TestClient, db_session: Any) -> None:
    _seed_user(db_session)
    client.post("/api/v1/admin/members", json={"email": _addr("new")})
    imported = next(
        m
        for m in client.get("/api/v1/admin/members").json()["members"]
        if m["source"] == "auto_import"
    )

    body = client.post(f"/api/v1/admin/members/{imported['id']}/confirm").json()
    assert body["source"] == "admin"


def test_removing_a_member_returns_204_and_drops_the_row(
    client: TestClient, db_session: Any
) -> None:
    _seed_user(db_session, role="admin")
    added = client.post("/api/v1/admin/members", json={"email": _addr("go")}).json()["member"]

    removed = client.delete(f"/api/v1/admin/members/{added['id']}")
    assert removed.status_code == 204
    remaining = [m["email"] for m in client.get("/api/v1/admin/members").json()["members"]]
    assert added["email"] not in remaining


def test_removing_the_last_admin_is_a_409(client: TestClient) -> None:
    """The caller is the only admin here, so their own membership is what the
    guard protects — and `confirm_self` gets past the self-check, not past this.
    """
    client.post("/api/v1/admin/members", json={"email": _addr("new")})
    members = client.get("/api/v1/admin/members").json()["members"]
    row = next(m for m in members if m["stored_role"] == "admin")

    response = client.delete(f"/api/v1/admin/members/{row['id']}?confirm_self=true")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "membership_change_rejected"


def test_removing_your_own_membership_needs_the_explicit_flag(client: TestClient) -> None:
    client.post("/api/v1/admin/members", json={"email": _addr("new")})
    members = client.get("/api/v1/admin/members").json()["members"]
    mine = next(m for m in members if m["stored_role"] == "admin")

    refused = client.delete(f"/api/v1/admin/members/{mine['id']}")
    assert refused.status_code == 409
    assert "confirm_self" in refused.json()["error"]["message"]


def test_an_unknown_member_id_is_a_404(client: TestClient) -> None:
    response = client.delete(f"/api/v1/admin/members/{uuid.uuid4()}")
    assert response.status_code == 404


def test_a_duplicate_add_is_a_409(client: TestClient, db_session: Any) -> None:
    _seed_user(db_session)
    email = _addr("dup")
    client.post("/api/v1/admin/members", json={"email": email})

    again = client.post("/api/v1/admin/members", json={"email": email})
    assert again.status_code == 409


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "no-at-sign"},
        {"email": "a" * 400 + "@example.com"},
        {"email": "nul\x00byte@example.com"},
        {"email": _addr(), "initial_role": "superuser"},
    ],
)
def test_bad_input_is_refused_with_a_4xx_not_a_500(
    client: TestClient, payload: dict[str, Any]
) -> None:
    """Boundary values must never reach the driver as an opaque internal error."""
    response = client.post("/api/v1/admin/members", json=payload)
    assert response.status_code in (409, 422)


# ── authz ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_a_non_admin_is_refused_on_every_membership_route(
    client: TestClient, as_role: Any, role: str
) -> None:
    _, headers = as_role(role)
    member_id = uuid.uuid4()

    listed = client.get("/api/v1/admin/members", headers=headers)
    added = client.post("/api/v1/admin/members", json={"email": _addr()}, headers=headers)
    removed = client.delete(f"/api/v1/admin/members/{member_id}", headers=headers)
    confirmed = client.post(f"/api/v1/admin/members/{member_id}/confirm", headers=headers)

    assert [r.status_code for r in (listed, added, removed, confirmed)] == [403, 403, 403, 403]


# ── audit ─────────────────────────────────────────────────────────────────────


def test_each_route_records_its_audit_event(client: TestClient, db_session: Any) -> None:
    _seed_user(db_session, role="admin")
    added = client.post("/api/v1/admin/members", json={"email": _addr("new")}).json()["member"]
    imported = next(
        m
        for m in client.get("/api/v1/admin/members").json()["members"]
        if m["source"] == "auto_import"
    )
    client.post(f"/api/v1/admin/members/{imported['id']}/confirm")
    client.delete(f"/api/v1/admin/members/{added['id']}")

    actions = set(db_session.scalars(select(AuditEvent.action)).all())
    assert {
        "workspace_member.add",
        "workspace_member.confirm",
        "workspace_member.remove",
    } <= actions
