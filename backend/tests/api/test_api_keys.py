"""/me/api-keys route tests + the PAT authenticator end-to-end (ADR 0026, #461).

Runs under the conftest dev-bypass auth mode. The PAT branch sits in front of
the bypass (same seam order as real mode), so presenting `Authorization:
Bearer dq_live_…` must resolve to the key's OWNER — not the dev user — and a
bad PAT must 401 rather than fall through to the bypass identity.
"""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.core.auth import DEV_BYPASS_EMAIL
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import api_key_service as svc


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _pat_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_mint_returns_token_once_and_list_never_does(client: TestClient) -> None:
    created = client.post("/api/v1/me/api-keys", json={"name": "ci-smoke"})
    assert created.status_code == 201
    body = created.json()
    assert body["token"].startswith(svc.TOKEN_PREFIX)
    assert body["name"] == "ci-smoke"
    assert body["key_prefix"] == body["token"][: len(body["key_prefix"])]
    assert body["revoked_at"] is None

    listed = client.get("/api/v1/me/api-keys")
    assert listed.status_code == 200
    (row,) = listed.json()
    assert row["id"] == body["id"]
    assert "token" not in row  # metadata only — the plaintext appeared exactly once
    assert "key_hash" not in row


def test_expiry_bounds_are_422(client: TestClient) -> None:
    assert (
        client.post("/api/v1/me/api-keys", json={"name": "x", "expires_in_days": 0}).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/me/api-keys",
            json={"name": "x", "expires_in_days": svc.MAX_EXPIRY_DAYS + 1},
        ).status_code
        == 422
    )


def test_pat_authenticates_as_its_owner_not_the_bypass_user(
    client: TestClient, db_session: Any
) -> None:
    owner = User(id=uuid.uuid4(), aad_object_id=f"oid-{uuid.uuid4().hex[:8]}", email="pat@x.io")
    db_session.add(owner)
    db_session.commit()
    _, token = svc.create_key(db_session, owner, name="e2e")

    body = client.get("/api/v1/me", headers=_pat_header(token)).json()
    assert body["email"] == "pat@x.io"
    assert body["email"] != DEV_BYPASS_EMAIL


def test_bad_pat_401s_instead_of_falling_through_to_bypass(client: TestClient) -> None:
    r = client.get("/api/v1/me", headers=_pat_header(svc.TOKEN_PREFIX + "bogus"))
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_api_key"


def test_revoked_pat_stops_authenticating_immediately(client: TestClient) -> None:
    body = client.post("/api/v1/me/api-keys", json={"name": "doomed"}).json()
    token = body["token"]
    assert client.get("/api/v1/me", headers=_pat_header(token)).status_code == 200

    assert client.delete(f"/api/v1/me/api-keys/{body['id']}").status_code == 204
    assert client.get("/api/v1/me", headers=_pat_header(token)).status_code == 401
    # Idempotent re-revoke.
    assert client.delete(f"/api/v1/me/api-keys/{body['id']}").status_code == 204


def test_revoking_another_users_key_404s(client: TestClient, db_session: Any) -> None:
    owner = User(id=uuid.uuid4(), aad_object_id=f"oid-{uuid.uuid4().hex[:8]}", email="o@x.io")
    db_session.add(owner)
    db_session.commit()
    key, _ = svc.create_key(db_session, owner, name="theirs")

    r = client.delete(f"/api/v1/me/api-keys/{key.id}")
    assert r.status_code == 404


def test_pat_lifecycle_via_pat_auth_itself(client: TestClient, db_session: Any) -> None:
    """A PAT can manage keys too (it IS the user): mint over PAT auth works."""
    owner = User(id=uuid.uuid4(), aad_object_id=f"oid-{uuid.uuid4().hex[:8]}", email="s@x.io")
    db_session.add(owner)
    db_session.commit()
    _, token = svc.create_key(db_session, owner, name="root")

    r = client.post("/api/v1/me/api-keys", json={"name": "child"}, headers=_pat_header(token))
    assert r.status_code == 201
    names = {
        k["name"] for k in client.get("/api/v1/me/api-keys", headers=_pat_header(token)).json()
    }
    assert names == {"root", "child"}
