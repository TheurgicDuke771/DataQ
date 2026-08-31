"""Incident API tests against a real Postgres (db_session) via TestClient."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from backend.app.core.auth import get_current_user
from backend.app.db.models import Check, Connection, Result, Run, Share, Suite, User
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import incident_service, suite_service

_SF_CONFIG = {"account": "ab12345.eu-west-1", "database": "ANALYTICS", "schema": "PUBLIC"}
_ADMIN_EMAIL = "admin@example.com"


def _author(row: Any) -> uuid.UUID:
    """`created_by` is `UUID | None` since #1319 (SET NULL on user delete), but a
    row this test just seeded always has one — narrowed here so a real None fails
    loudly in the test rather than inside the service.
    """
    author = row.created_by
    assert author is not None
    return cast(uuid.UUID, author)


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
    return incident_service.list_incidents(db, user_id=_author(suite), include_all=True)[0]


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
    resp = client.get("/api/v1/incidents")
    assert resp.json() == []


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
    resp = client.get("/api/v1/incidents", params={"asset_id": asset_id})
    assert len(resp.json()) == 1
    resp = client.get("/api/v1/incidents", params={"state": "open"})
    assert len(resp.json()) == 1
    resp = client.get("/api/v1/incidents", params={"state": "resolved"})
    assert resp.json() == []


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


def test_detail_redacts_a_same_asset_sibling_from_a_suite_the_caller_cannot_view(
    client: TestClient, world: dict[str, Any]
) -> None:
    """#1635: `same_asset_siblings` is workspace-true at write time; the read
    surface must withhold a sibling suite the caller has no grant on.
    """
    db = client_db(client)
    other_owner = _user(db, "other-owner@example.com")
    other_conn = _connection(db, other_owner)
    other_suite = _suite(db, other_owner, other_conn, table="ORDERS")
    assert other_suite.asset_id == world["suite"].asset_id  # same table ⇒ same asset
    other_check = Check(
        suite_id=other_suite.id,
        name="orders_volume_ok",
        kind="expectation",
        expectation_type="expect_column_values_to_not_be_null",
        config={"column": "id"},
    )
    db.add(other_check)
    db.flush()
    other_run = Run(suite_id=other_suite.id, status="succeeded", asset_id=other_suite.asset_id)
    db.add(other_run)
    db.flush()
    db.add(Result(run_id=other_run.id, check_id=other_check.id, status="fail"))
    db.commit()

    # Re-breach the world incident's own check so its evidence re-snapshots + picks up the sibling.
    run = Run(suite_id=world["suite"].id, status="succeeded", asset_id=world["suite"].asset_id)
    db.add(run)
    db.flush()
    db.add(
        Result(run_id=run.id, check_id=world["incident"].check_id, status="fail", metric_value=0.5)
    )
    db.commit()
    incident_service.sync_incidents_for_run(db, run_id=run.id)

    _as(world["owner"])  # owner of world["suite"] only — no grant on other_suite
    resp = client.get(f"/api/v1/incidents/{world['incident'].id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["evidence"]["same_asset_siblings"] == []
    assert body["evidence"]["same_asset_siblings_restricted_count"] == 1

    _share(db, other_suite, world["owner"], "view")
    resp = client.get(f"/api/v1/incidents/{world['incident'].id}")
    body = resp.json()
    assert len(body["evidence"]["same_asset_siblings"]) == 1
    assert body["evidence"]["same_asset_siblings"][0]["suite_id"] == str(other_suite.id)
    assert body["evidence"]["same_asset_siblings_restricted_count"] == 0


def test_no_share_detail_404_no_leak(client: TestClient, world: dict[str, Any]) -> None:
    outsider = _user(client_db(client), "outsider2@example.com")
    _as(outsider)
    resp = client.get(f"/api/v1/incidents/{world['incident'].id}")
    assert resp.status_code == 404


def test_404_no_leak_bodies_identical(client: TestClient, world: dict[str, Any]) -> None:
    """An existing-but-ungranted incident and a truly unknown id return the same
    status AND body shape (only the echoed id varies).
    """
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
    resp = client.post(url, json={})
    assert resp.status_code == 200
    resp = client.post(url, json={})
    assert resp.status_code == 409


# ── adversarial input (#570 class) ────────────────────────────────────────────


def test_garbage_uuid_is_422(client: TestClient, world: dict[str, Any]) -> None:
    _as(world["owner"])
    resp = client.get("/api/v1/incidents/not-a-uuid")
    assert resp.status_code == 422
    resp = client.get("/api/v1/incidents/%00")
    assert resp.status_code == 422
    resp = client.post("/api/v1/incidents/not-a-uuid/ack", json={})
    assert resp.status_code == 422
    resp = client.get("/api/v1/incidents", params={"limit": 0})
    assert resp.status_code == 422
    resp = client.get("/api/v1/incidents", params={"limit": 501})
    assert resp.status_code == 422


def test_nul_and_oversized_note_422(client: TestClient, world: dict[str, Any]) -> None:
    _as(world["owner"])
    url = f"/api/v1/incidents/{world['incident'].id}/ack"
    resp = client.post(url, json={"note": "bad\x00value"})
    assert resp.status_code == 422
    resp = client.post(url, json={"note": "x" * 2001})
    assert resp.status_code == 422
    # The cap boundary itself is accepted.
    resp = client.post(url, json={"note": "x" * 2000})
    assert resp.status_code == 200


def test_unknown_field_rejected(client: TestClient, world: dict[str, Any]) -> None:
    _as(world["owner"])
    resp = client.post(f"/api/v1/incidents/{world['incident'].id}/ack", json={"notee": "typo"})
    assert resp.status_code == 422


# ── fix batch (PR #775 review): pagination (the #772 /assets gap, one PR later) ─


def test_list_pagination_limit_offset_and_truncation(
    client: TestClient, world: dict[str, Any]
) -> None:
    """limit caps (truncates) the newest-first list; offset pages the remainder
    deterministically; past-the-end is an empty page, not an error.
    """
    db = client_db(client)
    # Two more failing checks on the suite → three incidents total.
    for _ in range(2):
        _incident(db, world["suite"])
    _as(world["owner"])

    full = client.get("/api/v1/incidents").json()
    assert len(full) == 3

    page1 = client.get("/api/v1/incidents", params={"limit": 2}).json()
    assert len(page1) == 2  # truncated at the cap
    page2 = client.get("/api/v1/incidents", params={"limit": 2, "offset": 2}).json()
    assert len(page2) == 1
    # The pages tile the full ordering with no overlap or gap.
    assert [i["id"] for i in page1] + [i["id"] for i in page2] == [i["id"] for i in full]
    resp = client.get("/api/v1/incidents", params={"offset": 10})
    assert resp.json() == []
    # Bounds still validate (#570 class).
    resp = client.get("/api/v1/incidents", params={"offset": -1})
    assert resp.status_code == 422


def test_list_orders_by_most_recent_breach_not_when_first_opened(
    client: TestClient, world: dict[str, Any]
) -> None:
    """#1442: `list_incidents` (shared by REST and MCP) sorts by `last_seen_at`,
    not `created_at` — an incident opened earlier that breached again most
    recently must lead the list, since "is this still happening" is about
    recent activity, not when the pair first paired up.
    """
    db = client_db(client)
    older = world["incident"]  # opened first, in the `world` fixture
    # NOT `_incident()`'s return value: with `include_all=True` and no suite
    # scoping, `[0]` is whichever of the two ties the `last_seen_at`/id
    # tie-break — resolve the second incident deterministically instead, by
    # the one check id that isn't `older`'s.
    _incident(db, world["suite"])
    newer = next(
        i
        for i in incident_service.list_incidents(
            db, user_id=_author(world["suite"]), suite_id=world["suite"].id, include_all=True
        )
        if i.check_id != older.check_id
    )
    # Force the ordering explicitly rather than relying on wall-clock drift
    # between the two `_incident()` calls, which can tie at microsecond
    # precision.
    now = datetime.now(UTC)
    older.created_at = now - timedelta(hours=2)
    older.last_seen_at = now - timedelta(hours=2)
    newer.created_at = now - timedelta(hours=1)
    newer.last_seen_at = now - timedelta(hours=1)
    db.commit()

    # Re-breach the OLDER incident's check — attaches an occurrence and bumps
    # its last_seen_at ahead of the newer incident's.
    run = Run(
        suite_id=world["suite"].id,
        status="succeeded",
        triggered_by="t",
        asset_id=world["suite"].asset_id,
    )
    db.add(run)
    db.flush()
    db.add(Result(run_id=run.id, check_id=older.check_id, status="fail", metric_value=0.5))
    db.commit()
    incident_service.sync_incidents_for_run(db, run_id=run.id)

    _as(world["owner"])
    ids = [i["id"] for i in client.get("/api/v1/incidents").json()]
    assert ids.index(str(older.id)) < ids.index(str(newer.id))


def test_total_count_header_matches_accessible_population(
    client: TestClient, world: dict[str, Any]
) -> None:
    """#1108: `/incidents` had `offset` (#772) but no total — a page shorter than `limit` couldn't
    be told apart from "that's everything". `X-Total-Count` reports the caller's ACCESSIBLE
    population (suite-grant-scoped, like the list itself — unlike the workspace-true `/assets`
    total), unaffected by the page size.
    """
    db = client_db(client)
    for _ in range(2):
        _incident(db, world["suite"])  # 3 incidents total on the accessible suite

    other = _user(db, "stranger@example.com")
    other_conn = _connection(db, other)
    other_suite = _suite(db, other, other_conn)
    _incident(db, other_suite)  # not accessible to the owner — must not inflate the total

    _as(world["owner"])
    resp = client.get("/api/v1/incidents", params={"limit": 2})
    assert resp.status_code == 200
    assert resp.headers["x-total-count"] == "3"
    assert len(resp.json()) == 2  # the page is still truncated to `limit`


def test_total_count_header_respects_filters(client: TestClient, world: dict[str, Any]) -> None:
    """The header counts the SAME filtered population the list applies (#1108) —
    a `state` filter narrows both, not just the page.
    """
    db = client_db(client)
    _incident(db, world["suite"])  # a second open incident
    _as(world["owner"])

    resolve_url = f"/api/v1/incidents/{world['incident'].id}/resolve"
    client.post(resolve_url, json={})

    resp = client.get("/api/v1/incidents", params={"state": "open"})
    assert resp.headers["x-total-count"] == "1"
    resp = client.get("/api/v1/incidents", params={"state": "resolved"})
    assert resp.headers["x-total-count"] == "1"
    resp = client.get("/api/v1/incidents")
    assert resp.headers["x-total-count"] == "2"
