"""Notification-config endpoint tests (TestClient against real Postgres)."""

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


def _user(db: Any, email: str, *, role: str = "member") -> User:
    u = User(aad_object_id=uuid.uuid4().hex, email=email, role=role)
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


def test_put_rejects_bad_policy(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "o@ex")
    _as(owner)
    sid = _suite(db_session, owner)
    resp = client.put(f"/api/v1/suites/{sid}/notifications", json={"alert_on": "nope"})
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


def _editor(client: TestClient, db_session: Any) -> tuple[User, str]:
    owner = _user(db_session, "o@ex")
    editor = _user(db_session, "e@ex")
    _as(owner)
    sid = _suite(db_session, owner)
    client.post(
        f"/api/v1/suites/{sid}/shares", json={"user_id": str(editor.id), "permission": "edit"}
    )
    _as(editor)
    return editor, sid


@pytest.mark.parametrize(
    "body",
    [
        {"webhook": "https://x.webhook.office.com/hook"},
        {"slack_webhook": "https://hooks.slack.com/services/T/B/x"},
        {"email_recipients": "a@x.io"},
    ],
)
def test_nobody_can_set_an_inline_destination(
    client: TestClient, db_session: Any, body: dict[str, str]
) -> None:
    """Destinations are the channels an Admin configured — the suite takes none of
    its own, for editors AND admins (#1926)."""
    _, sid = _editor(client, db_session)
    resp = client.put(f"/api/v1/suites/{sid}/notifications", json={"alert_on": "fail", **body})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "inline_destination_not_allowed"
    assert resp.json()["error"]["detail"]["fields"] == list(body)
    app.dependency_overrides.pop(get_current_user, None)  # dev-bypass: a workspace admin
    as_admin = client.put(f"/api/v1/suites/{sid}/notifications", json={"alert_on": "fail", **body})
    assert as_admin.status_code == 422
    untouched = client.get(f"/api/v1/suites/{sid}/notifications").json()
    assert untouched["configured"] is False


def test_a_legacy_inline_destination_is_reported_and_clearable(
    client: TestClient, db_session: Any
) -> None:
    """A row written before #1926 still reads back (so the UI can show it) and
    clears through the route."""
    from backend.app.services import notification_service
    from backend.tests.support.fake_secret_store import FakeSecretStore

    owner = _user(db_session, "o@ex")
    _as(owner)
    sid = _suite(db_session, owner)
    notification_service.upsert_config(
        db_session,
        suite_id=uuid.UUID(sid),
        enabled=True,
        alert_on="fail",
        webhook="https://x.webhook.office.com/hook",
        email_recipients="team@x.io",
        secret_store=FakeSecretStore(),
    )
    before = client.get(f"/api/v1/suites/{sid}/notifications").json()
    assert (before["has_webhook"], before["email_recipients"]) == (True, "team@x.io")
    after = client.put(
        f"/api/v1/suites/{sid}/notifications",
        json={"enabled": True, "alert_on": "fail", "webhook": "", "email_recipients": ""},
    ).json()
    assert (after["has_webhook"], after["email_recipients"]) == (False, None)


def test_an_editor_can_still_clear_an_inline_destination(
    client: TestClient, db_session: Any
) -> None:
    """Clearing is how a suite moves off the legacy path — open to editors."""
    _, sid = _editor(client, db_session)
    resp = client.put(
        f"/api/v1/suites/{sid}/notifications",
        json={"alert_on": "fail", "webhook": "", "slack_webhook": "", "email_recipients": ""},
    )
    assert resp.status_code == 200
    assert resp.json()["configured"] is True
