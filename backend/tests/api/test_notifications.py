"""Notification-config endpoint tests (TestClient against real Postgres).

Auth runs in dev-bypass (conftest). Skips without TEST_DATABASE_URL.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.core.auth import get_current_user
from backend.app.db.models import Connection, Suite, User
from backend.app.db.session import get_db
from backend.app.main import app


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _as(user: User) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


def _suite(db: Any, owner: User) -> str:
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "a"},
        created_by=owner.id,
    )
    db.add(conn)
    db.flush()
    suite = Suite(name="s", connection_id=conn.id, created_by=owner.id)
    db.add(suite)
    db.commit()
    return str(suite.id)


def _user(db: Any, email: str) -> User:
    u = User(aad_object_id=uuid.uuid4().hex, email=email)
    db.add(u)
    db.commit()
    return u


def test_get_returns_defaults_when_unconfigured(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "o@ex")
    _as(owner)
    sid = _suite(db_session, owner)
    body = client.get(f"/api/v1/suites/{sid}/notifications").json()
    assert body == {
        "configured": False,
        "enabled": True,
        "alert_on": "warn",
        "has_webhook": False,
        "has_slack_webhook": False,
        "email_recipients": None,
    }


def test_put_then_get_roundtrips(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "o@ex")
    _as(owner)
    sid = _suite(db_session, owner)
    put = client.put(
        f"/api/v1/suites/{sid}/notifications",
        json={
            "enabled": True,
            "alert_on": "always",
            "webhook": "https://contoso.webhook.office.com/hook",
        },
    )
    assert put.status_code == 200
    assert put.json() == {
        "configured": True,
        "enabled": True,
        "alert_on": "always",
        "has_webhook": True,  # URL stored in the SecretStore, only the flag returned
        "has_slack_webhook": False,
        "email_recipients": None,
    }
    # The webhook URL is never echoed back.
    assert "webhook" not in put.json()
    got = client.get(f"/api/v1/suites/{sid}/notifications").json()
    assert got["configured"] is True and got["alert_on"] == "always"


def test_put_rejects_bad_policy(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "o@ex")
    _as(owner)
    sid = _suite(db_session, owner)
    resp = client.put(f"/api/v1/suites/{sid}/notifications", json={"alert_on": "nope"})
    assert resp.status_code == 422


def test_put_slack_and_email_roundtrip(client: TestClient, db_session: Any) -> None:
    """#633: per-suite Slack webhook (write-only flag) + email recipients (returned)."""
    owner = _user(db_session, "o@ex")
    _as(owner)
    sid = _suite(db_session, owner)
    put = client.put(
        f"/api/v1/suites/{sid}/notifications",
        json={
            "enabled": True,
            "alert_on": "warn",
            "slack_webhook": "https://hooks.slack.com/services/T/B/xyz",
            "email_recipients": "a@x.io, b@y.io",
        },
    )
    assert put.status_code == 200
    body = put.json()
    assert body["has_slack_webhook"] is True  # webhook stored, only the flag returned
    assert body["email_recipients"] == "a@x.io, b@y.io"  # not a secret → echoed for prefill
    assert "slack_webhook" not in body  # the URL is never echoed back


def test_put_rejects_bad_slack_host_and_email(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "o@ex")
    _as(owner)
    sid = _suite(db_session, owner)
    assert (
        client.put(
            f"/api/v1/suites/{sid}/notifications",
            json={"slack_webhook": "https://evil.example.com/x"},
        ).status_code
        == 422
    )
    assert (
        client.put(
            f"/api/v1/suites/{sid}/notifications",
            json={"email_recipients": "not-an-email"},
        ).status_code
        == 422
    )


def test_put_rejects_non_https_webhook(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "o@ex")
    _as(owner)
    sid = _suite(db_session, owner)
    resp = client.put(
        f"/api/v1/suites/{sid}/notifications",
        json={"alert_on": "fail", "webhook": "http://insecure"},
    )
    assert resp.status_code == 422


def test_put_rejects_non_allowlisted_webhook_host(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "o@ex")
    _as(owner)
    sid = _suite(db_session, owner)
    resp = client.put(
        f"/api/v1/suites/{sid}/notifications",
        json={"alert_on": "fail", "webhook": "https://attacker.example/exfil"},
    )
    assert resp.status_code == 422


def test_delete_reverts_to_defaults(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "o@ex")
    _as(owner)
    sid = _suite(db_session, owner)
    client.put(f"/api/v1/suites/{sid}/notifications", json={"alert_on": "fail"})
    delete_resp = client.delete(f"/api/v1/suites/{sid}/notifications")
    assert delete_resp.status_code == 204
    get_resp = client.get(f"/api/v1/suites/{sid}/notifications")
    assert get_resp.json()["configured"] is False


def test_viewer_can_read_not_write(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "o@ex")
    viewer = _user(db_session, "v@ex")
    _as(owner)
    sid = _suite(db_session, owner)
    client.post(
        f"/api/v1/suites/{sid}/shares", json={"user_id": str(viewer.id), "permission": "view"}
    )
    _as(viewer)
    read_resp = client.get(f"/api/v1/suites/{sid}/notifications")
    assert read_resp.status_code == 200
    write_resp = client.put(f"/api/v1/suites/{sid}/notifications", json={"alert_on": "fail"})
    assert write_resp.status_code == 403


def test_outsider_gets_404(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "o@ex")
    outsider = _user(db_session, "x@ex")
    _as(owner)
    sid = _suite(db_session, owner)
    _as(outsider)
    get_resp = client.get(f"/api/v1/suites/{sid}/notifications")
    assert get_resp.status_code == 404
