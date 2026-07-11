"""Incident API tests against a real Postgres (db_session) via TestClient.

The **authz matrix mirrors the asset-view rules exactly** (ADR 0034 decision 5 /
ADR 0027, #760): read visibility is derived from suite grants; ack/resolve require
``edit`` on the incident's suite; an incident wholly outside the caller's grants is
404-no-leak (bodies asserted identical, like test_assets.py). Plus the adversarial
input floor (#570 class): garbage UUIDs / NUL bytes / over-cap notes are 4xx, never
500.

Skips without TEST_DATABASE_URL."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.core.auth import get_current_user
from backend.app.db.models import Check, Connection, Result, Run, Share, Suite, User
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import incident_service, suite_service

_SF_CONFIG = {"account": "ab12345.eu-west-1", "database": "ANALYTICS", "schema": "PUBLIC"}
_ADMIN_EMAIL = "admin@example.com"


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _as(user: User) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


def client_db(client: TestClient) -> Any:
    return app.dependency_overrides[get_db]()


def _user(db: Any, email: str) -> User:
    u = User(aad_object_id=uuid.uuid4().hex, email=email)
    db.add(u)
    db.flush()
    return u


def _connection(db: Any, owner: User) -> Connection:
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config=_SF_CONFIG,
        secret_ref="kv-x",
        created_by=owner.id,
    )
    db.add(conn)
    db.commit()
    return conn


def _suite(db: Any, owner: User, conn: Connection, *, table: str = "ORDERS") -> Suite:
    return suite_service.create_suite(
        db,
        name=f"suite-{uuid.uuid4().hex[:6]}",
        description=None,
        connection_id=conn.id,
        created_by=owner.id,
        target={"table": table},
    )


def _incident(db: Any, suite: Suite, *, status: str = "fail") -> Any:
    """Seed a failing run for the suite and sync it into an open incident."""
    check = Check(
        suite_id=suite.id,
        name=f"c-{uuid.uuid4().hex[:6]}",
        kind="expectation",
        expectation_type="expect_column_values_to_not_be_null",
        config={"column": "id"},
    )
    db.add(check)
    db.flush()
    run = Run(suite_id=suite.id, status="succeeded", triggered_by="t", asset_id=suite.asset_id)
    db.add(run)
    db.flush()
    db.add(Result(run_id=run.id, check_id=check.id, status=status, metric_value=0.4))
    db.commit()
    incident_service.sync_incidents_for_run(db, run_id=run.id)
    return incident_service.list_incidents(db, user_id=suite.created_by, include_all=True)[0]


@pytest.fixture
def world(db_session: Any) -> dict[str, Any]:
    owner = _user(db_session, "owner@example.com")
    conn = _connection(db_session, owner)
    suite = _suite(db_session, owner, conn)
    incident = _incident(db_session, suite)
    return {"owner": owner, "conn": conn, "suite": suite, "incident": incident}


def _share(db: Any, suite: Suite, user: User, permission: str) -> None:
    db.add(Share(suite_id=suite.id, user_id=user.id, permission=permission))
    db.commit()


# ── list authz ────────────────────────────────────────────────────────────────


def test_owner_lists_incident(client: TestClient, world: dict[str, Any]) -> None:
    _as(world["owner"])
    resp = client.get("/api/v1/incidents")
    assert resp.status_code == 200
    ids = [i["id"] for i in resp.json()]
    assert str(world["incident"].id) in ids


def test_view_share_lists_incident(client: TestClient, world: dict[str, Any]) -> None:
    viewer = _user(client_db(client), "viewer@example.com")
    _share(client_db(client), world["suite"], viewer, "view")
    _as(viewer)
    resp = client.get("/api/v1/incidents")
    assert resp.status_code == 200
    assert str(world["incident"].id) in {i["id"] for i in resp.json()}


def test_no_share_lists_nothing(client: TestClient, world: dict[str, Any]) -> None:
    outsider = _user(client_db(client), "outsider@example.com")
    _as(outsider)
    assert client.get("/api/v1/incidents").json() == []


def test_workspace_admin_lists_all(
    client: TestClient, world: dict[str, Any], make_workspace_admin: Any
) -> None:
    admin = _user(client_db(client), _ADMIN_EMAIL)
    make_workspace_admin(_ADMIN_EMAIL)
    _as(admin)
    resp = client.get("/api/v1/incidents")
    assert str(world["incident"].id) in {i["id"] for i in resp.json()}


def test_list_filters_by_asset_and_state(client: TestClient, world: dict[str, Any]) -> None:
    _as(world["owner"])
    asset_id = str(world["suite"].asset_id)
    assert len(client.get("/api/v1/incidents", params={"asset_id": asset_id}).json()) == 1
    assert len(client.get("/api/v1/incidents", params={"state": "open"}).json()) == 1
    assert client.get("/api/v1/incidents", params={"state": "resolved"}).json() == []


def test_list_invalid_state_422(client: TestClient, world: dict[str, Any]) -> None:
    _as(world["owner"])
    resp = client.get("/api/v1/incidents", params={"state": "bogus"})
    assert resp.status_code == 422


# ── detail + no-leak ──────────────────────────────────────────────────────────


def test_owner_detail_has_evidence(client: TestClient, world: dict[str, Any]) -> None:
    _as(world["owner"])
    resp = client.get(f"/api/v1/incidents/{world['incident'].id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "open"
    assert body["evidence"] is not None
    assert body["check_name"] is not None


def test_no_share_detail_404_no_leak(client: TestClient, world: dict[str, Any]) -> None:
    outsider = _user(client_db(client), "outsider2@example.com")
    _as(outsider)
    resp = client.get(f"/api/v1/incidents/{world['incident'].id}")
    assert resp.status_code == 404


def test_404_no_leak_bodies_identical(client: TestClient, world: dict[str, Any]) -> None:
    """An existing-but-ungranted incident and a truly unknown id return the same
    status AND body shape (only the echoed id varies)."""
    outsider = _user(client_db(client), "outsider3@example.com")
    _as(outsider)
    unknown_id = uuid.uuid4()
    existing = client.get(f"/api/v1/incidents/{world['incident'].id}")
    unknown = client.get(f"/api/v1/incidents/{unknown_id}")
    assert existing.status_code == unknown.status_code == 404

    def normalized(resp: Any) -> tuple[str, dict[str, Any]]:
        body: dict[str, Any] = resp.json()
        echoed: str = body["error"]["detail"].pop("incident_id")
        return echoed, body

    existing_echo, existing_body = normalized(existing)
    unknown_echo, unknown_body = normalized(unknown)
    assert existing_echo == str(world["incident"].id)
    assert unknown_echo == str(unknown_id)
    assert existing_body == unknown_body


# ── ack / resolve authz (edit-gated) ──────────────────────────────────────────


def test_owner_can_ack_and_resolve(client: TestClient, world: dict[str, Any]) -> None:
    _as(world["owner"])
    url = f"/api/v1/incidents/{world['incident'].id}"
    ack = client.post(f"{url}/ack", json={"note": "on it"})
    assert ack.status_code == 200
    assert ack.json()["status"] == "acknowledged"
    resolve = client.post(f"{url}/resolve", json={"note": "fixed"})
    assert resolve.status_code == 200
    assert resolve.json()["status"] == "resolved"
    assert resolve.json()["resolved_by"] == "user"


def test_edit_share_can_ack(client: TestClient, world: dict[str, Any]) -> None:
    editor = _user(client_db(client), "editor@example.com")
    _share(client_db(client), world["suite"], editor, "edit")
    _as(editor)
    resp = client.post(f"/api/v1/incidents/{world['incident'].id}/ack", json={})
    assert resp.status_code == 200


def test_view_share_cannot_ack_403(client: TestClient, world: dict[str, Any]) -> None:
    viewer = _user(client_db(client), "viewer3@example.com")
    _share(client_db(client), world["suite"], viewer, "view")
    _as(viewer)
    resp = client.post(f"/api/v1/incidents/{world['incident'].id}/ack", json={})
    assert resp.status_code == 403


def test_no_share_cannot_ack_404_no_leak(client: TestClient, world: dict[str, Any]) -> None:
    outsider = _user(client_db(client), "outsider4@example.com")
    _as(outsider)
    resp = client.post(f"/api/v1/incidents/{world['incident'].id}/ack", json={})
    assert resp.status_code == 404  # not 403 — existence hidden


def test_workspace_admin_can_resolve(
    client: TestClient, world: dict[str, Any], make_workspace_admin: Any
) -> None:
    admin = _user(client_db(client), _ADMIN_EMAIL)
    make_workspace_admin(_ADMIN_EMAIL)
    _as(admin)
    resp = client.post(f"/api/v1/incidents/{world['incident'].id}/resolve", json={})
    assert resp.status_code == 200


def test_double_resolve_409(client: TestClient, world: dict[str, Any]) -> None:
    _as(world["owner"])
    url = f"/api/v1/incidents/{world['incident'].id}/resolve"
    assert client.post(url, json={}).status_code == 200
    assert client.post(url, json={}).status_code == 409


# ── adversarial input (#570 class) ────────────────────────────────────────────


def test_garbage_uuid_is_422(client: TestClient, world: dict[str, Any]) -> None:
    _as(world["owner"])
    assert client.get("/api/v1/incidents/not-a-uuid").status_code == 422
    assert client.get("/api/v1/incidents/%00").status_code == 422
    assert client.post("/api/v1/incidents/not-a-uuid/ack", json={}).status_code == 422
    assert client.get("/api/v1/incidents", params={"limit": 0}).status_code == 422
    assert client.get("/api/v1/incidents", params={"limit": 501}).status_code == 422


def test_nul_and_oversized_note_422(client: TestClient, world: dict[str, Any]) -> None:
    _as(world["owner"])
    url = f"/api/v1/incidents/{world['incident'].id}/ack"
    assert client.post(url, json={"note": "bad\x00value"}).status_code == 422
    assert client.post(url, json={"note": "x" * 2001}).status_code == 422
    # The cap boundary itself is accepted.
    assert client.post(url, json={"note": "x" * 2000}).status_code == 200


def test_unknown_field_rejected(client: TestClient, world: dict[str, Any]) -> None:
    _as(world["owner"])
    resp = client.post(f"/api/v1/incidents/{world['incident'].id}/ack", json={"notee": "typo"})
    assert resp.status_code == 422
