"""Connection endpoint tests against a real Postgres (db_session) via TestClient.

get_db + get_secret_store are overridden to a shared test session and an
in-memory store; the connectivity adapter is monkeypatched so /test needs no
live warehouse. Auth runs in dev-bypass mode (conftest), which upserts the dev
user into the same session for the created_by FK. Skips without
TEST_DATABASE_URL.
"""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.core.auth import get_current_user
from backend.app.core.secrets import get_secret_store
from backend.app.db.models import Connection
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import connection_service as svc

_SF_CONFIG = {
    "account": "ab12345.eu-west-1",
    "user": "svc_dataq",
    "database": "ANALYTICS",
    "schema": "FINANCE",
    "warehouse": "WH_DQ",
    "role": "DQ_ROLE",
}

_ADF_CONFIG = {
    "subscription_id": "00000000-0000-0000-0000-000000000001",
    "resource_group": "rg-data",
    "factory_name": "example-adf-preprod",
    "tenant_id": "00000000-0000-0000-0000-0000000000aa",
    "client_id": "00000000-0000-0000-0000-0000000000bb",
}


def _adf_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "adf-pre-prod",
        "type": "adf",
        "env": "dev",
        "config": dict(_ADF_CONFIG),
        "secret": "sp-secret",
    }
    payload.update(overrides)
    return payload


class FakeStore:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, name: str) -> str:
        return self.data[name]

    def set(self, name: str, value: str) -> None:
        self.data[name] = value


class _WriteFailStore(FakeStore):
    """SecretStore whose set() fails — simulates Key Vault unreachable (#87)."""

    def set(self, name: str, value: str) -> None:
        from backend.app.core.secrets import SecretWriteError

        raise SecretWriteError("key vault unreachable")


class _PassAdapter:
    def validate_config(self, raw: dict[str, Any]) -> Any:
        return None

    def test(self, raw: dict[str, Any], secret: str) -> None:
        return None


class _FailAdapter(_PassAdapter):
    def test(self, raw: dict[str, Any], secret: str) -> None:
        raise RuntimeError("warehouse unreachable")


@pytest.fixture
def client(db_session: Any) -> Iterator[tuple[TestClient, FakeStore]]:
    store = FakeStore()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_secret_store] = lambda: store
    try:
        yield TestClient(app), store
    finally:
        app.dependency_overrides.clear()


def _create_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "finance-dev",
        "type": "snowflake",
        "env": "dev",
        "config": dict(_SF_CONFIG),
        "secret": "p@ss",
    }
    payload.update(overrides)
    return payload


# ───────────────────────── create ──────────────────────────────────


def test_create_returns_201_and_hides_secret(
    client: tuple[TestClient, FakeStore], db_session: Any
) -> None:
    api, store = client
    resp = api.post("/api/v1/connections", json=_create_payload())

    assert resp.status_code == 201
    body = resp.json()
    assert body["type"] == "snowflake"
    assert body["has_secret"] is True
    # secret material must never appear in the response
    assert "secret" not in body
    assert "secret_ref" not in body
    # persisted + written through to the store
    conn = db_session.get(Connection, uuid.UUID(body["id"]))
    assert conn is not None
    assert store.data[f"conn-{conn.id}"] == "p@ss"


def test_create_unknown_type_returns_422(client: tuple[TestClient, FakeStore]) -> None:
    api, _ = client
    resp = api.post("/api/v1/connections", json=_create_payload(type="mssql"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "connection_config_invalid"


def test_create_secret_write_failure_returns_502(db_session: Any) -> None:
    # Key Vault write failure must surface as a 502 envelope, not a generic 500 (#87).
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_secret_store] = _WriteFailStore
    try:
        resp = TestClient(app).post("/api/v1/connections", json=_create_payload())
        assert resp.status_code == 502
        assert resp.json()["error"]["code"] == "connection_secret_write_failed"
        # the half-inserted row must not survive
        assert db_session.scalars(select(Connection)).all() == []
    finally:
        app.dependency_overrides.clear()


def test_create_invalid_config_returns_422(client: tuple[TestClient, FakeStore]) -> None:
    api, _ = client
    bad = {k: v for k, v in _SF_CONFIG.items() if k != "account"}
    resp = api.post("/api/v1/connections", json=_create_payload(config=bad))
    assert resp.status_code == 422


def test_create_invalid_env_returns_422(client: tuple[TestClient, FakeStore]) -> None:
    api, _ = client
    resp = api.post("/api/v1/connections", json=_create_payload(env="staging"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "connection_config_invalid"


def test_create_duplicate_returns_409(client: tuple[TestClient, FakeStore]) -> None:
    api, _ = client
    first = api.post("/api/v1/connections", json=_create_payload(name="dup"))
    assert first.status_code == 201
    resp = api.post("/api/v1/connections", json=_create_payload(name="dup"))
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "connection_conflict"


# ───────────────────────── ADF connection (#72) ────────────────────


def test_create_adf_returns_201(client: tuple[TestClient, FakeStore]) -> None:
    api, _ = client
    resp = api.post("/api/v1/connections", json=_adf_payload())
    assert resp.status_code == 201
    body = resp.json()
    assert body["type"] == "adf"
    assert body["config"]["factory_name"] == "example-adf-preprod"
    assert body["has_secret"] is True


def test_second_adf_same_env_returns_409(client: tuple[TestClient, FakeStore]) -> None:
    api, _ = client
    first = api.post("/api/v1/connections", json=_adf_payload(name="adf-a"))
    assert first.status_code == 201
    # different name, same (type, env): the orchestrator singleton guard fires.
    resp = api.post("/api/v1/connections", json=_adf_payload(name="adf-b"))
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "connection_conflict"
    assert "adf" in resp.json()["error"]["message"]


def test_adf_second_env_returns_201(client: tuple[TestClient, FakeStore]) -> None:
    api, _ = client
    api.post("/api/v1/connections", json=_adf_payload(name="adf-dev", env="dev"))
    resp = api.post("/api/v1/connections", json=_adf_payload(name="adf-qa", env="qa"))
    assert resp.status_code == 201


def test_list_filters_by_adf_type(client: tuple[TestClient, FakeStore]) -> None:
    api, _ = client
    api.post("/api/v1/connections", json=_create_payload(name="sf"))
    api.post("/api/v1/connections", json=_adf_payload(name="adf"))
    adf_only = api.get("/api/v1/connections", params={"type": "adf"}).json()
    assert [c["name"] for c in adf_only] == ["adf"]
    assert all(c["type"] == "adf" for c in adf_only)


# ───────────────────────── read / list ─────────────────────────────


def test_list_returns_created(client: tuple[TestClient, FakeStore]) -> None:
    api, _ = client
    api.post("/api/v1/connections", json=_create_payload(name="a"))
    api.post("/api/v1/connections", json=_create_payload(name="b", env="qa"))

    all_conns = api.get("/api/v1/connections").json()
    assert {c["name"] for c in all_conns} == {"a", "b"}
    qa = api.get("/api/v1/connections", params={"env": "qa"}).json()
    assert [c["name"] for c in qa] == ["b"]


def test_get_returns_connection(client: tuple[TestClient, FakeStore]) -> None:
    api, _ = client
    cid = api.post("/api/v1/connections", json=_create_payload()).json()["id"]
    resp = api.get(f"/api/v1/connections/{cid}")
    assert resp.status_code == 200
    assert resp.json()["id"] == cid


def test_get_unknown_returns_404(client: tuple[TestClient, FakeStore]) -> None:
    api, _ = client
    resp = api.get(f"/api/v1/connections/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "connection_not_found"


# ───────────────────────── update / delete ─────────────────────────


def test_patch_updates_name(client: tuple[TestClient, FakeStore]) -> None:
    api, _ = client
    cid = api.post("/api/v1/connections", json=_create_payload()).json()["id"]
    resp = api.patch(f"/api/v1/connections/{cid}", json={"name": "renamed"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "renamed"


def test_delete_returns_204_then_404(client: tuple[TestClient, FakeStore]) -> None:
    api, _ = client
    cid = api.post("/api/v1/connections", json=_create_payload()).json()["id"]
    deleted = api.delete(f"/api/v1/connections/{cid}")
    assert deleted.status_code == 204
    gone = api.get(f"/api/v1/connections/{cid}")
    assert gone.status_code == 404


# ───────────────────────── test connectivity ───────────────────────


def test_test_endpoint_ok(
    client: tuple[TestClient, FakeStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    api, _ = client
    cid = api.post("/api/v1/connections", json=_create_payload()).json()["id"]
    monkeypatch.setattr(svc, "get_connection_adapter", lambda t: _PassAdapter())
    resp = api.post(f"/api/v1/connections/{cid}/test")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_test_endpoint_failure_returns_502(
    client: tuple[TestClient, FakeStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    api, _ = client
    cid = api.post("/api/v1/connections", json=_create_payload()).json()["id"]
    monkeypatch.setattr(svc, "get_connection_adapter", lambda t: _FailAdapter())
    resp = api.post(f"/api/v1/connections/{cid}/test")
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "connection_test_failed"


# ───────────────────────── re-auth (rotate + verify) ───────────────


def test_reauth_rotates_credential_and_verifies(
    client: tuple[TestClient, FakeStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    api, store = client
    cid = api.post("/api/v1/connections", json=_create_payload()).json()["id"]
    assert store.data[f"conn-{cid}"] == "p@ss"  # original credential

    monkeypatch.setattr(svc, "get_connection_adapter", lambda t: _PassAdapter())
    resp = api.post(f"/api/v1/connections/{cid}/reauth", json={"secret": "rotated"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert store.data[f"conn-{cid}"] == "rotated"  # credential rotated in the store


def test_reauth_failed_verify_returns_502_but_rotation_persists(
    client: tuple[TestClient, FakeStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    api, store = client
    cid = api.post("/api/v1/connections", json=_create_payload()).json()["id"]

    # The new credential is stored, then the probe rejects it → 502. The rotation
    # is intentionally kept (the old credential was already expired).
    monkeypatch.setattr(svc, "get_connection_adapter", lambda t: _FailAdapter())
    resp = api.post(f"/api/v1/connections/{cid}/reauth", json={"secret": "still-bad"})
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "connection_test_failed"
    assert store.data[f"conn-{cid}"] == "still-bad"


def test_reauth_secret_write_failure_returns_502(
    client: tuple[TestClient, FakeStore],
) -> None:
    api, _ = client
    cid = api.post("/api/v1/connections", json=_create_payload()).json()["id"]
    # Swap in a store whose set() fails only for the re-auth call (Key Vault down).
    app.dependency_overrides[get_secret_store] = _WriteFailStore
    resp = api.post(f"/api/v1/connections/{cid}/reauth", json={"secret": "rotated"})
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "connection_secret_write_failed"


def test_reauth_unknown_connection_returns_404(client: tuple[TestClient, FakeStore]) -> None:
    api, _ = client
    resp = api.post(f"/api/v1/connections/{uuid.uuid4()}/reauth", json={"secret": "x"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "connection_not_found"


def test_reauth_requires_a_secret(client: tuple[TestClient, FakeStore]) -> None:
    api, _ = client
    cid = api.post("/api/v1/connections", json=_create_payload()).json()["id"]
    assert api.post(f"/api/v1/connections/{cid}/reauth", json={}).status_code == 422
    assert api.post(f"/api/v1/connections/{cid}/reauth", json={"secret": ""}).status_code == 422


# ───────────────────────── auth gating ─────────────────────────────


def test_create_requires_auth(db_session: Any) -> None:
    store = FakeStore()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_secret_store] = lambda: store

    def _reject() -> None:
        raise HTTPException(status_code=401, detail="unauthorized")

    app.dependency_overrides[get_current_user] = _reject
    try:
        resp = TestClient(app).post("/api/v1/connections", json=_create_payload())
        assert resp.status_code == 401
        rows = db_session.scalars(select(Connection)).all()
        assert rows == []  # handler must not have created a row
    finally:
        app.dependency_overrides.clear()
