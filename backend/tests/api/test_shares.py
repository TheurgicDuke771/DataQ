"""Suite-share endpoint tests against a real Postgres via TestClient.

Access control needs *different* actors, so each request overrides
get_current_user to the acting user (the dev-bypass default is bypassed). The
owner is a real User; B/C/E are other users; a connection + suite are seeded
directly. Skips without TEST_DATABASE_URL.
"""

import uuid
from collections.abc import Callable, Iterator
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


def _user(db_session: Any, email: str, display_name: str | None = None) -> User:
    u = User(aad_object_id=uuid.uuid4().hex, email=email, display_name=display_name)
    db_session.add(u)
    db_session.flush()
    return u


def _seed(db_session: Any) -> tuple[User, User, User, User, Suite]:
    owner = _user(db_session, "owner@ex")
    b = _user(db_session, "b@ex", display_name="Bee")
    c = _user(db_session, "c@ex")
    e = _user(db_session, "e@ex")  # no access
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
    return owner, b, c, e, suite


def _as(user: User) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


# ───────────────────────── grant ──────────────────────────────────


def test_owner_grants_share(client: TestClient, db_session: Any) -> None:
    owner, b, _c, _e, suite = _seed(db_session)
    _as(owner)
    resp = client.post(
        f"/api/v1/suites/{suite.id}/shares", json={"user_id": str(b.id), "permission": "view"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["user_id"] == str(b.id)
    assert body["permission"] == "view"
    # Enriched with the grantee's directory identity (joined from Share.user) so
    # the sharing UI can name collaborators without a second lookup.
    assert body["email"] == "b@ex"
    assert body["display_name"] == "Bee"


def test_grant_admin_rejected(client: TestClient, db_session: Any) -> None:
    # `admin` is no longer grantable to a normal user (ADR 0027 / #482) — it's the
    # workspace-admin, implicit on every suite. The owner's attempt is a 422.
    owner, b, _c, _e, suite = _seed(db_session)
    _as(owner)
    resp = client.post(
        f"/api/v1/suites/{suite.id}/shares", json={"user_id": str(b.id), "permission": "admin"}
    )
    assert resp.status_code == 422


def test_workspace_admin_can_grant_on_any_suite(
    client: TestClient, db_session: Any, make_workspace_admin: Callable[..., None]
) -> None:
    # A workspace-admin is an implicit `admin` on every suite (ADR 0027) — they can
    # manage shares on a suite they neither own nor are shared on.
    _owner, b, c, _e, suite = _seed(db_session)
    make_workspace_admin(c.email)
    _as(c)  # C owns nothing here, has no share — only the allowlist makes them admin
    resp = client.post(
        f"/api/v1/suites/{suite.id}/shares", json={"user_id": str(b.id), "permission": "view"}
    )
    assert resp.status_code == 201
    assert resp.json()["permission"] == "view"


def test_viewer_cannot_grant(client: TestClient, db_session: Any) -> None:
    owner, b, c, _e, suite = _seed(db_session)
    _as(owner)
    client.post(
        f"/api/v1/suites/{suite.id}/shares", json={"user_id": str(b.id), "permission": "view"}
    )
    _as(b)  # B has only view → cannot manage shares
    resp = client.post(
        f"/api/v1/suites/{suite.id}/shares", json={"user_id": str(c.id), "permission": "view"}
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "suite_forbidden"


def test_no_access_user_gets_404_not_403(client: TestClient, db_session: Any) -> None:
    _owner, _b, _c, e, suite = _seed(db_session)
    _as(e)  # E has no share and isn't the owner → existence hidden
    resp = client.get(f"/api/v1/suites/{suite.id}/shares")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "suite_not_found"


def test_grant_to_owner_rejected(client: TestClient, db_session: Any) -> None:
    owner, _b, _c, _e, suite = _seed(db_session)
    _as(owner)
    resp = client.post(
        f"/api/v1/suites/{suite.id}/shares", json={"user_id": str(owner.id), "permission": "view"}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "share_target_invalid"


def test_grant_to_unknown_user_422(client: TestClient, db_session: Any) -> None:
    owner, _b, _c, _e, suite = _seed(db_session)
    _as(owner)
    resp = client.post(
        f"/api/v1/suites/{suite.id}/shares",
        json={"user_id": str(uuid.uuid4()), "permission": "view"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "share_target_invalid"


def test_grant_on_unknown_suite_404(client: TestClient, db_session: Any) -> None:
    owner, b, _c, _e, _suite = _seed(db_session)
    _as(owner)
    resp = client.post(
        f"/api/v1/suites/{uuid.uuid4()}/shares", json={"user_id": str(b.id), "permission": "view"}
    )
    assert resp.status_code == 404


def test_duplicate_grant_409(client: TestClient, db_session: Any) -> None:
    owner, b, _c, _e, suite = _seed(db_session)
    _as(owner)
    first = client.post(
        f"/api/v1/suites/{suite.id}/shares", json={"user_id": str(b.id), "permission": "view"}
    )
    assert first.status_code == 201
    dup = client.post(
        f"/api/v1/suites/{suite.id}/shares", json={"user_id": str(b.id), "permission": "edit"}
    )
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "share_conflict"


def test_invalid_permission_422(client: TestClient, db_session: Any) -> None:
    owner, b, _c, _e, suite = _seed(db_session)
    _as(owner)
    # 'admin' is now ungrantable too (workspace-admin only) — alongside 'owner'.
    for bad in ("owner", "admin", "superuser", ""):
        resp = client.post(
            f"/api/v1/suites/{suite.id}/shares", json={"user_id": str(b.id), "permission": bad}
        )
        assert resp.status_code == 422


# ───────────────────────── list / update / revoke ─────────────────


def test_list_shares_visible_to_collaborators(client: TestClient, db_session: Any) -> None:
    owner, b, _c, _e, suite = _seed(db_session)
    _as(owner)
    client.post(
        f"/api/v1/suites/{suite.id}/shares", json={"user_id": str(b.id), "permission": "view"}
    )
    _as(b)  # B (view) can see the collaborator list
    resp = client.get(f"/api/v1/suites/{suite.id}/shares")
    assert resp.status_code == 200
    assert {s["user_id"] for s in resp.json()} == {str(b.id)}


def test_update_permission(client: TestClient, db_session: Any) -> None:
    owner, b, _c, _e, suite = _seed(db_session)
    _as(owner)
    client.post(
        f"/api/v1/suites/{suite.id}/shares", json={"user_id": str(b.id), "permission": "view"}
    )
    resp = client.patch(f"/api/v1/suites/{suite.id}/shares/{b.id}", json={"permission": "edit"})
    assert resp.status_code == 200
    assert resp.json()["permission"] == "edit"


def test_update_missing_share_404(client: TestClient, db_session: Any) -> None:
    owner, b, _c, _e, suite = _seed(db_session)
    _as(owner)
    resp = client.patch(f"/api/v1/suites/{suite.id}/shares/{b.id}", json={"permission": "edit"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "share_not_found"


def test_admin_cannot_self_downgrade(
    client: TestClient, db_session: Any, make_workspace_admin: Callable[..., None]
) -> None:
    # The self-target guard (#240) rejects an admin-capable actor managing their
    # OWN share. With grantable admin removed (ADR 0027), the non-owner admin is
    # now the workspace-admin — who self-targeting is still refused (422).
    _owner, _b, c, _e, suite = _seed(db_session)
    make_workspace_admin(c.email)
    _as(c)
    resp = client.patch(f"/api/v1/suites/{suite.id}/shares/{c.id}", json={"permission": "view"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "share_target_invalid"


def test_admin_cannot_self_revoke(
    client: TestClient, db_session: Any, make_workspace_admin: Callable[..., None]
) -> None:
    # Likewise, a workspace-admin can't revoke their own share row (#240).
    _owner, _b, c, _e, suite = _seed(db_session)
    make_workspace_admin(c.email)
    _as(c)
    resp = client.delete(f"/api/v1/suites/{suite.id}/shares/{c.id}")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "share_target_invalid"


def test_revoke_then_no_access(client: TestClient, db_session: Any) -> None:
    owner, b, _c, _e, suite = _seed(db_session)
    _as(owner)
    client.post(
        f"/api/v1/suites/{suite.id}/shares", json={"user_id": str(b.id), "permission": "view"}
    )
    revoked = client.delete(f"/api/v1/suites/{suite.id}/shares/{b.id}")
    assert revoked.status_code == 204
    _as(b)  # B's access is gone → suite is hidden
    gone = client.get(f"/api/v1/suites/{suite.id}/shares")
    assert gone.status_code == 404
