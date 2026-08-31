"""Notification channel endpoint tests (#1514) — Admin-gated CRUD, suite link/unlink
on the suite's own view/edit ladder. TestClient against real Postgres.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.db.models import Connection, Suite, User
from backend.app.db.session import get_db
from backend.app.main import app
from backend.tests.support.fake_secret_store import FakeSecretStore, override_secret_store

_TEAMS_URL = "https://contoso.webhook.office.com/x"

ROLES = ("admin", "member", "viewer")


@pytest.fixture
def secret_store() -> FakeSecretStore:
    return FakeSecretStore()


@pytest.fixture
def client(db_session: Any, secret_store: FakeSecretStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    override_secret_store(app, secret_store)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _suite(db: Any, owner: User) -> Suite:
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "a"},
        created_by=owner.id,
    )
    db.add(conn)
    db.flush()
    suite = Suite(name="s", connection_id=conn.id, created_by=owner.id, target={"table": "T"})
    db.add(suite)
    db.commit()
    return suite


def _channel_payload(**overrides: Any) -> dict[str, Any]:
    return {"name": "Platform Teams", "type": "teams", "webhook": _TEAMS_URL, **overrides}


@pytest.mark.parametrize("role", ROLES)
def test_create_channel_is_admin_only(client: TestClient, as_role: Any, role: str) -> None:
    _, headers = as_role(role)
    resp = client.post("/api/v1/notification-channels", json=_channel_payload(), headers=headers)
    assert resp.status_code == (201 if role == "admin" else 403)


def test_create_channel_never_echoes_the_webhook(client: TestClient, as_role: Any) -> None:
    _, headers = as_role("admin")
    resp = client.post("/api/v1/notification-channels", json=_channel_payload(), headers=headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["has_webhook"] is True
    assert "webhook" not in body


@pytest.mark.parametrize("role", ROLES)
def test_list_and_get_channels_are_open_to_every_tier(
    client: TestClient, as_role: Any, role: str
) -> None:
    _admin, admin_headers = as_role("admin")
    created = client.post(
        "/api/v1/notification-channels", json=_channel_payload(), headers=admin_headers
    ).json()
    _, headers = as_role(role)
    listed = client.get("/api/v1/notification-channels", headers=headers)
    assert listed.status_code == 200
    assert any(c["id"] == created["id"] for c in listed.json())
    got = client.get(f"/api/v1/notification-channels/{created['id']}", headers=headers)
    assert got.status_code == 200
    assert "webhook" not in got.json()


@pytest.mark.parametrize("role", ROLES)
def test_update_channel_is_admin_only(client: TestClient, as_role: Any, role: str) -> None:
    _admin, admin_headers = as_role("admin")
    created = client.post(
        "/api/v1/notification-channels", json=_channel_payload(), headers=admin_headers
    ).json()
    _, headers = as_role(role)
    resp = client.patch(
        f"/api/v1/notification-channels/{created['id']}",
        json={"name": "renamed"},
        headers=headers,
    )
    assert resp.status_code == (200 if role == "admin" else 403)


@pytest.mark.parametrize("role", ROLES)
def test_delete_channel_is_admin_only(client: TestClient, as_role: Any, role: str) -> None:
    _admin, admin_headers = as_role("admin")
    created = client.post(
        "/api/v1/notification-channels", json=_channel_payload(), headers=admin_headers
    ).json()
    _, headers = as_role(role)
    resp = client.delete(f"/api/v1/notification-channels/{created['id']}", headers=headers)
    assert resp.status_code == (204 if role == "admin" else 403)


def test_delete_channel_refuses_while_linked_to_a_suite(
    client: TestClient, db_session: Any, as_role: Any
) -> None:
    admin, headers = as_role("admin")
    created = client.post(
        "/api/v1/notification-channels", json=_channel_payload(), headers=headers
    ).json()
    suite = _suite(db_session, admin)
    link = client.put(
        f"/api/v1/suites/{suite.id}/notification-channels/{created['id']}", headers=headers
    )
    assert link.status_code == 204
    resp = client.delete(f"/api/v1/notification-channels/{created['id']}", headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "channel_in_use"


def test_rotate_webhook_via_update_never_echoes_it(client: TestClient, as_role: Any) -> None:
    _admin, headers = as_role("admin")
    created = client.post(
        "/api/v1/notification-channels", json=_channel_payload(), headers=headers
    ).json()
    resp = client.patch(
        f"/api/v1/notification-channels/{created['id']}",
        json={"webhook": "https://contoso.webhook.office.com/rotated"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["has_webhook"] is True
    assert "webhook" not in resp.json()


def test_clear_webhook_via_empty_string(client: TestClient, as_role: Any) -> None:
    _admin, headers = as_role("admin")
    created = client.post(
        "/api/v1/notification-channels", json=_channel_payload(), headers=headers
    ).json()
    resp = client.patch(
        f"/api/v1/notification-channels/{created['id']}", json={"webhook": ""}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["has_webhook"] is False


def test_create_channel_rejects_a_non_allowlisted_webhook(client: TestClient, as_role: Any) -> None:
    _, headers = as_role("admin")
    resp = client.post(
        "/api/v1/notification-channels",
        json=_channel_payload(webhook="https://evil.example.com/x"),
        headers=headers,
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("role", ("admin", "member"))
def test_link_suite_channel_requires_edit(
    client: TestClient, db_session: Any, as_role: Any, role: str
) -> None:
    _admin, admin_headers = as_role("admin")
    created = client.post(
        "/api/v1/notification-channels", json=_channel_payload(), headers=admin_headers
    ).json()
    actor, headers = as_role(role)
    suite = _suite(db_session, actor)
    resp = client.put(
        f"/api/v1/suites/{suite.id}/notification-channels/{created['id']}", headers=headers
    )
    assert resp.status_code == 204


def test_viewer_cannot_link_a_channel_to_a_suite(
    client: TestClient, db_session: Any, as_role: Any
) -> None:
    admin, admin_headers = as_role("admin")
    created = client.post(
        "/api/v1/notification-channels", json=_channel_payload(), headers=admin_headers
    ).json()
    suite = _suite(db_session, admin)
    viewer, viewer_headers = as_role("viewer")
    grant = client.post(
        f"/api/v1/suites/{suite.id}/shares",
        json={"user_id": str(viewer.id), "permission": "view"},
        headers=admin_headers,
    )
    assert grant.status_code == 201
    resp = client.put(
        f"/api/v1/suites/{suite.id}/notification-channels/{created['id']}",
        headers=viewer_headers,
    )
    assert resp.status_code == 403


def test_link_then_list_then_unlink_roundtrip(
    client: TestClient, db_session: Any, as_role: Any
) -> None:
    admin, headers = as_role("admin")
    created = client.post(
        "/api/v1/notification-channels", json=_channel_payload(), headers=headers
    ).json()
    suite = _suite(db_session, admin)
    link_url = f"/api/v1/suites/{suite.id}/notification-channels/{created['id']}"
    suite_channels_url = f"/api/v1/suites/{suite.id}/notification-channels"
    channel_url = f"/api/v1/notification-channels/{created['id']}"

    link_resp = client.put(link_url, headers=headers)
    assert link_resp.status_code == 204
    linked = client.get(suite_channels_url, headers=headers)
    assert linked.status_code == 200
    assert [c["id"] for c in linked.json()] == [created["id"]]
    unlink_resp = client.delete(link_url, headers=headers)
    assert unlink_resp.status_code == 204
    after_unlink = client.get(suite_channels_url, headers=headers)
    assert after_unlink.json() == []
    # No longer referenced, so the delete this test's own name promised now succeeds.
    delete_resp = client.delete(channel_url, headers=headers)
    assert delete_resp.status_code == 204


def test_link_suite_channel_is_idempotent_over_rest(
    client: TestClient, db_session: Any, as_role: Any
) -> None:
    admin, headers = as_role("admin")
    created = client.post(
        "/api/v1/notification-channels", json=_channel_payload(), headers=headers
    ).json()
    suite = _suite(db_session, admin)
    url = f"/api/v1/suites/{suite.id}/notification-channels/{created['id']}"
    first = client.put(url, headers=headers)
    assert first.status_code == 204
    second = client.put(url, headers=headers)
    assert second.status_code == 204  # still 204, not a conflict
    linked = client.get(f"/api/v1/suites/{suite.id}/notification-channels", headers=headers)
    assert [c["id"] for c in linked.json()] == [created["id"]]


def test_outsider_gets_404_on_suite_scoped_channel_routes(
    client: TestClient, db_session: Any, as_role: Any
) -> None:
    admin, admin_headers = as_role("admin")
    created = client.post(
        "/api/v1/notification-channels", json=_channel_payload(), headers=admin_headers
    ).json()
    suite = _suite(db_session, admin)
    _outsider, outsider_headers = as_role("member")
    resp = client.get(f"/api/v1/suites/{suite.id}/notification-channels", headers=outsider_headers)
    assert resp.status_code == 404
    resp = client.put(
        f"/api/v1/suites/{suite.id}/notification-channels/{created['id']}",
        headers=outsider_headers,
    )
    assert resp.status_code == 404


# ── generic webhook channels (#1662) ─────────────────────────────────────────
# 8.8.8.8 — a stable, unambiguously public IP literal, SSRF-guard-safe and
# DNS-free (matches the notification_service/channel_service SSRF test convention).
_WEBHOOK_URL = "https://8.8.8.8/hook"


def test_create_webhook_channel_returns_the_hmac_secret_exactly_once(
    client: TestClient, as_role: Any
) -> None:
    _admin, headers = as_role("admin")
    resp = client.post(
        "/api/v1/notification-channels",
        json={"name": "Ops", "type": "webhook", "webhook_url": _WEBHOOK_URL},
        headers=headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["webhook_url"] == _WEBHOOK_URL
    assert body["has_hmac_secret"] is True
    assert body["hmac_secret"] is not None

    # Never re-shown on a subsequent read.
    got = client.get(f"/api/v1/notification-channels/{body['id']}", headers=headers)
    assert got.status_code == 200
    assert got.json()["hmac_secret"] is None
    assert got.json()["has_hmac_secret"] is True


def test_create_webhook_channel_rejects_an_internal_url(client: TestClient, as_role: Any) -> None:
    _admin, headers = as_role("admin")
    resp = client.post(
        "/api/v1/notification-channels",
        json={"name": "Ops", "type": "webhook", "webhook_url": "https://127.0.0.1/hook"},
        headers=headers,
    )
    assert resp.status_code == 422


def test_webhook_url_on_the_wrong_type_is_a_field_mismatch(
    client: TestClient, as_role: Any
) -> None:
    _admin, headers = as_role("admin")
    resp = client.post(
        "/api/v1/notification-channels",
        json=_channel_payload(webhook_url=_WEBHOOK_URL),  # channel type here is teams
        headers=headers,
    )
    assert resp.status_code == 422


def test_regenerate_hmac_secret_rotates_and_returns_the_new_key_once(
    client: TestClient, as_role: Any
) -> None:
    _admin, headers = as_role("admin")
    created = client.post(
        "/api/v1/notification-channels",
        json={"name": "Ops", "type": "webhook", "webhook_url": _WEBHOOK_URL},
        headers=headers,
    ).json()
    first_secret = created["hmac_secret"]

    resp = client.patch(
        f"/api/v1/notification-channels/{created['id']}",
        json={"regenerate_hmac_secret": True},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["hmac_secret"] is not None
    assert body["hmac_secret"] != first_secret
    assert body["has_hmac_secret"] is True


def test_delete_webhook_channel_is_admin_only(client: TestClient, as_role: Any) -> None:
    _admin, admin_headers = as_role("admin")
    created = client.post(
        "/api/v1/notification-channels",
        json={"name": "Ops", "type": "webhook", "webhook_url": _WEBHOOK_URL},
        headers=admin_headers,
    ).json()
    resp = client.delete(f"/api/v1/notification-channels/{created['id']}", headers=admin_headers)
    assert resp.status_code == 204


# ── payload template + auth header (#1663) ───────────────────────────────────

_TEMPLATE = {"routing_key": "static", "payload": {"summary": "{{suite_name}}"}}


def test_create_webhook_channel_with_a_payload_template_and_auth_header(
    client: TestClient, as_role: Any
) -> None:
    _admin, headers = as_role("admin")
    resp = client.post(
        "/api/v1/notification-channels",
        json={
            "name": "PagerDuty",
            "type": "webhook",
            "webhook_url": _WEBHOOK_URL,
            "payload_template": _TEMPLATE,
            "auth_header_name": "X-Api-Key",
            "auth_header_value": "sk-abc123",
        },
        headers=headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["payload_template"] == _TEMPLATE
    assert body["auth_header_name"] == "X-Api-Key"
    assert body["has_auth_header"] is True
    assert "auth_header_value" not in body  # write-only, never echoed


def test_create_channel_rejects_a_reserved_auth_header(client: TestClient, as_role: Any) -> None:
    _admin, headers = as_role("admin")
    resp = client.post(
        "/api/v1/notification-channels",
        json={
            "name": "Ops",
            "type": "webhook",
            "webhook_url": _WEBHOOK_URL,
            "auth_header_name": "Content-Type",
            "auth_header_value": "x",
        },
        headers=headers,
    )
    assert resp.status_code == 422


def test_create_channel_rejects_a_payload_template_on_teams(
    client: TestClient, as_role: Any
) -> None:
    _admin, headers = as_role("admin")
    resp = client.post(
        "/api/v1/notification-channels",
        json=_channel_payload(payload_template=_TEMPLATE),  # channel type here is teams
        headers=headers,
    )
    assert resp.status_code == 422


def test_update_channel_clears_the_payload_template_via_rest(
    client: TestClient, as_role: Any
) -> None:
    _admin, headers = as_role("admin")
    created = client.post(
        "/api/v1/notification-channels",
        json={
            "name": "Ops",
            "type": "webhook",
            "webhook_url": _WEBHOOK_URL,
            "payload_template": _TEMPLATE,
        },
        headers=headers,
    ).json()
    resp = client.patch(
        f"/api/v1/notification-channels/{created['id']}",
        json={"clear_payload_template": True},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["payload_template"] is None
