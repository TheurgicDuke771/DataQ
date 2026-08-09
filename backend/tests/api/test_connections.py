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
from backend.app.core.secret_names import connection_secret_ref
from backend.app.core.secrets import SecretWriteError, get_secret_store
from backend.app.db.models import Connection
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import connection_service as svc
from backend.tests.support.fake_secret_store import FakeSecretStore

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


class _WriteFailStore(FakeSecretStore):
    """SecretStore whose set() fails — simulates Key Vault unreachable (#87).

    `.delete()` is inherited from `FakeSecretStore`: a `dict.pop(name, None)`
    against the store's always-empty `data` (nothing here ever writes
    successfully) is already the no-op the original hand-rolled `pass` was.
    """

    def set(self, name: str, value: str) -> None:
        raise SecretWriteError("key vault unreachable")


class _PassAdapter:
    def validate_config(self, raw: dict[str, Any]) -> Any:
        return None

    def test(self, raw: dict[str, Any], secret: str) -> None:
        return None


class _FailAdapter(_PassAdapter):
    def test(self, raw: dict[str, Any], secret: str) -> None:
        raise RuntimeError("warehouse unreachable")


class _OptionalSecretAdapter(_PassAdapter):
    """A `secret_optional=True` stand-in (the Iceberg/dbt shape, #351) — proves
    the route hands a credential-less adapter a real `None`, never "" or a
    placeholder, and never 502s for the missing secret."""

    secret_optional = True

    def __init__(self) -> None:
        self.received_secret: str | None | object = "UNSET"

    def test(self, raw: dict[str, Any], secret: str | None) -> None:
        self.received_secret = secret


@pytest.fixture
def client(db_session: Any) -> Iterator[tuple[TestClient, FakeSecretStore]]:
    store = FakeSecretStore()
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
    client: tuple[TestClient, FakeSecretStore], db_session: Any
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
    assert store.data[conn.secret_ref] == "p@ss"


def test_create_unknown_type_returns_422(client: tuple[TestClient, FakeSecretStore]) -> None:
    api, _ = client
    resp = api.post("/api/v1/connections", json=_create_payload(type="mssql"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "connection_config_invalid"


def test_create_secret_write_failure_returns_502(db_session: Any) -> None:
    # Key Vault write failure must surface as a 502 envelope, not a generic 500 (#87).
    app.dependency_overrides[get_db] = lambda: db_session
    # A lambda, not the bare class: FastAPI's dependency-override resolution
    # introspects the override CALLABLE's own signature (not the original
    # dependency's), so a class whose `__init__` takes parameters — even
    # all-defaulted ones like `FakeSecretStore`'s — gets those parameters
    # bound as request-level params, corrupting body validation for the
    # endpoint under test (discovered via #1251: a bare `_WriteFailStore`
    # override made `POST /connections` 422 instead of ever reaching the
    # route).
    app.dependency_overrides[get_secret_store] = lambda: _WriteFailStore()
    try:
        resp = TestClient(app).post("/api/v1/connections", json=_create_payload())
        assert resp.status_code == 502
        assert resp.json()["error"]["code"] == "connection_secret_write_failed"
        # the half-inserted row must not survive
        assert db_session.scalars(select(Connection)).all() == []
    finally:
        app.dependency_overrides.clear()


def test_create_invalid_config_returns_422(client: tuple[TestClient, FakeSecretStore]) -> None:
    api, _ = client
    bad = {k: v for k, v in _SF_CONFIG.items() if k != "account"}
    resp = api.post("/api/v1/connections", json=_create_payload(config=bad))
    assert resp.status_code == 422


def test_create_invalid_env_returns_422(client: tuple[TestClient, FakeSecretStore]) -> None:
    api, _ = client
    resp = api.post("/api/v1/connections", json=_create_payload(env="staging"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "connection_config_invalid"


def test_create_duplicate_returns_409(client: tuple[TestClient, FakeSecretStore]) -> None:
    api, _ = client
    first = api.post("/api/v1/connections", json=_create_payload(name="dup"))
    assert first.status_code == 201
    resp = api.post("/api/v1/connections", json=_create_payload(name="dup"))
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "connection_conflict"


# ───────────────────────── ADF connection (#72) ────────────────────


def test_create_adf_returns_201(client: tuple[TestClient, FakeSecretStore]) -> None:
    api, _ = client
    resp = api.post("/api/v1/connections", json=_adf_payload())
    assert resp.status_code == 201
    body = resp.json()
    assert body["type"] == "adf"
    assert body["config"]["factory_name"] == "example-adf-preprod"
    assert body["has_secret"] is True


def test_second_adf_same_env_returns_409(client: tuple[TestClient, FakeSecretStore]) -> None:
    api, _ = client
    first = api.post("/api/v1/connections", json=_adf_payload(name="adf-a"))
    assert first.status_code == 201
    # different name, same (type, env): the orchestrator singleton guard fires.
    resp = api.post("/api/v1/connections", json=_adf_payload(name="adf-b"))
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "connection_conflict"
    assert "adf" in resp.json()["error"]["message"]


def test_adf_second_env_returns_201(client: tuple[TestClient, FakeSecretStore]) -> None:
    api, _ = client
    api.post("/api/v1/connections", json=_adf_payload(name="adf-dev", env="dev"))
    resp = api.post("/api/v1/connections", json=_adf_payload(name="adf-qa", env="qa"))
    assert resp.status_code == 201


def test_list_filters_by_adf_type(client: tuple[TestClient, FakeSecretStore]) -> None:
    api, _ = client
    api.post("/api/v1/connections", json=_create_payload(name="sf"))
    api.post("/api/v1/connections", json=_adf_payload(name="adf"))
    adf_only = api.get("/api/v1/connections", params={"type": "adf"}).json()
    assert [c["name"] for c in adf_only] == ["adf"]
    assert all(c["type"] == "adf" for c in adf_only)


# ───────────────────────── read / list ─────────────────────────────


def test_list_returns_created(client: tuple[TestClient, FakeSecretStore]) -> None:
    api, _ = client
    api.post("/api/v1/connections", json=_create_payload(name="a"))
    api.post("/api/v1/connections", json=_create_payload(name="b", env="qa"))

    all_conns = api.get("/api/v1/connections").json()
    assert {c["name"] for c in all_conns} == {"a", "b"}
    qa = api.get("/api/v1/connections", params={"env": "qa"}).json()
    assert [c["name"] for c in qa] == ["b"]


def test_get_returns_connection(client: tuple[TestClient, FakeSecretStore]) -> None:
    api, _ = client
    cid = api.post("/api/v1/connections", json=_create_payload()).json()["id"]
    resp = api.get(f"/api/v1/connections/{cid}")
    assert resp.status_code == 200
    assert resp.json()["id"] == cid


def test_get_unknown_returns_404(client: tuple[TestClient, FakeSecretStore]) -> None:
    api, _ = client
    resp = api.get(f"/api/v1/connections/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "connection_not_found"


# ───────────────────────── update / delete ─────────────────────────


def test_patch_updates_name(client: tuple[TestClient, FakeSecretStore]) -> None:
    api, _ = client
    cid = api.post("/api/v1/connections", json=_create_payload()).json()["id"]
    resp = api.patch(f"/api/v1/connections/{cid}", json={"name": "renamed"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "renamed"


def test_delete_returns_204_then_404(client: tuple[TestClient, FakeSecretStore]) -> None:
    api, _ = client
    cid = api.post("/api/v1/connections", json=_create_payload()).json()["id"]
    deleted = api.delete(f"/api/v1/connections/{cid}")
    assert deleted.status_code == 204
    gone = api.get(f"/api/v1/connections/{cid}")
    assert gone.status_code == 404


def test_delete_with_dependent_suite_is_409_envelope_not_500(
    client: tuple[TestClient, FakeSecretStore],
) -> None:
    """#753 at the wire: the FK conflict surfaces as the standard 409 error
    envelope naming the dependent suites — never an unhandled 500."""
    api, _ = client
    cid = api.post("/api/v1/connections", json=_create_payload()).json()["id"]
    suite = api.post("/api/v1/suites", json={"name": "uses-conn", "connection_id": cid})
    assert suite.status_code == 201

    resp = api.delete(f"/api/v1/connections/{cid}")
    assert resp.status_code == 409
    err = resp.json()["error"]
    assert err["code"] == "connection_in_use"
    assert err["detail"]["total"] == 1
    assert err["detail"]["suites"][0]["name"] == "uses-conn"

    # Removing the dependent unblocks the delete. The calls are hoisted OUT of the
    # asserts (CodeQL py/side-effect-in-assert): under `python -O` assert bodies are
    # stripped, so an in-assert request would silently never fire and the test would
    # "pass" having exercised nothing.
    suite_deleted = api.delete(f"/api/v1/suites/{suite.json()['id']}")
    assert suite_deleted.status_code == 204
    conn_deleted = api.delete(f"/api/v1/connections/{cid}")
    assert conn_deleted.status_code == 204


# ───────────────────────── test connectivity ───────────────────────


def test_test_endpoint_ok(
    client: tuple[TestClient, FakeSecretStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    api, _ = client
    cid = api.post("/api/v1/connections", json=_create_payload()).json()["id"]
    monkeypatch.setattr(svc, "get_connection_adapter", lambda t: _PassAdapter())
    resp = api.post(f"/api/v1/connections/{cid}/test")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_test_endpoint_failure_returns_502(
    client: tuple[TestClient, FakeSecretStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    api, _ = client
    cid = api.post("/api/v1/connections", json=_create_payload()).json()["id"]
    monkeypatch.setattr(svc, "get_connection_adapter", lambda t: _FailAdapter())
    resp = api.post(f"/api/v1/connections/{cid}/test")
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "connection_test_failed"


def _ref(cid: str, payload: dict[str, object] | None = None) -> str:
    """The vault key the service mints for a connection created from `_create_payload`.

    Derived rather than hardcoded: `secret_ref` is deliberately not exposed on the
    API response, and pinning a literal here is what made these tests break on a
    naming change they have nothing to do with.
    """
    body = payload or _create_payload()
    return connection_secret_ref(
        connection_id=cid,
        env=str(body["env"]),
        name=str(body["name"]),
        conn_type=str(body["type"]),
    )


# ───────────────────────── re-auth (rotate + verify) ───────────────


def test_reauth_rotates_credential_and_verifies(
    client: tuple[TestClient, FakeSecretStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    api, store = client
    cid = api.post("/api/v1/connections", json=_create_payload()).json()["id"]
    assert store.data[_ref(cid)] == "p@ss"  # original credential

    monkeypatch.setattr(svc, "get_connection_adapter", lambda t: _PassAdapter())
    resp = api.post(f"/api/v1/connections/{cid}/reauth", json={"secret": "rotated"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert store.data[_ref(cid)] == "rotated"  # credential rotated in the store


def test_reauth_failed_verify_returns_502_but_rotation_persists(
    client: tuple[TestClient, FakeSecretStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    api, store = client
    cid = api.post("/api/v1/connections", json=_create_payload()).json()["id"]

    # The new credential is stored, then the probe rejects it → 502. The rotation
    # is intentionally kept (the old credential was already expired).
    monkeypatch.setattr(svc, "get_connection_adapter", lambda t: _FailAdapter())
    resp = api.post(f"/api/v1/connections/{cid}/reauth", json={"secret": "still-bad"})
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "connection_test_failed"
    assert store.data[_ref(cid)] == "still-bad"


def test_reauth_secret_write_failure_returns_502(
    client: tuple[TestClient, FakeSecretStore],
) -> None:
    api, _ = client
    cid = api.post("/api/v1/connections", json=_create_payload()).json()["id"]
    # Swap in a store whose set() fails only for the re-auth call (Key Vault
    # down). A lambda, not the bare class — see the comment on
    # `test_create_secret_write_failure_returns_502` for why.
    app.dependency_overrides[get_secret_store] = lambda: _WriteFailStore()
    resp = api.post(f"/api/v1/connections/{cid}/reauth", json={"secret": "rotated"})
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "connection_secret_write_failed"


def test_reauth_unknown_connection_returns_404(client: tuple[TestClient, FakeSecretStore]) -> None:
    api, _ = client
    resp = api.post(f"/api/v1/connections/{uuid.uuid4()}/reauth", json={"secret": "x"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "connection_not_found"


def test_reauth_requires_a_secret(client: tuple[TestClient, FakeSecretStore]) -> None:
    api, _ = client
    cid = api.post("/api/v1/connections", json=_create_payload()).json()["id"]
    resp = api.post(f"/api/v1/connections/{cid}/reauth", json={})
    assert resp.status_code == 422
    resp = api.post(f"/api/v1/connections/{cid}/reauth", json={"secret": ""})
    assert resp.status_code == 422


# ───────────────────────── draft connection test (#351) ────────────


def _draft_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "snowflake",
        "env": "dev",
        "config": dict(_SF_CONFIG),
        "secret": "p@ss",
    }
    payload.update(overrides)
    return payload


def test_draft_test_ok(
    client: tuple[TestClient, FakeSecretStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    api, _ = client
    monkeypatch.setattr(svc, "get_connection_adapter", lambda t: _PassAdapter())
    resp = api.post("/api/v1/connections/test", json=_draft_payload())
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_draft_test_failure_returns_502(
    client: tuple[TestClient, FakeSecretStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    api, _ = client
    monkeypatch.setattr(svc, "get_connection_adapter", lambda t: _FailAdapter())
    resp = api.post("/api/v1/connections/test", json=_draft_payload())
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "connection_test_failed"


def test_draft_test_missing_secret_returns_502(
    client: tuple[TestClient, FakeSecretStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    # An adapter that would happily pass never even runs — no credential means
    # nothing to probe with, same as the saved-connection contract.
    api, _ = client
    monkeypatch.setattr(svc, "get_connection_adapter", lambda t: _PassAdapter())
    resp = api.post("/api/v1/connections/test", json=_draft_payload(secret=None))
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "connection_test_failed"


def test_draft_test_snowflake_without_secret_still_502s_with_clear_message(
    client: tuple[TestClient, FakeSecretStore],
) -> None:
    """Snowflake's credential is NOT optional (#351 review) — must still 502
    with the clear "a credential is required" message rather than silently
    letting a `None` through to the adapter. No adapter monkeypatch: the
    REAL `SnowflakeConnectionAdapter` is used, and the 502 fires before the
    adapter is ever invoked (so no network call happens either way)."""
    api, _ = client
    resp = api.post("/api/v1/connections/test", json=_draft_payload(secret=None))
    assert resp.status_code == 502
    assert resp.json()["error"]["message"] == "a credential is required to test this connection"


# ──────── secret_optional — Iceberg/dbt credential-less configs (#351) ─────


def test_draft_test_secret_optional_adapter_allows_missing_secret(
    client: tuple[TestClient, FakeSecretStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Iceberg/dbt (`secret_optional`) — a legitimate credential-less draft
    must not 502 just because `secret` is absent, and the adapter must
    receive a real `None`, never "" or a placeholder."""
    api, _ = client
    adapter = _OptionalSecretAdapter()
    monkeypatch.setattr(svc, "get_connection_adapter", lambda t: adapter)
    resp = api.post(
        "/api/v1/connections/test",
        json=_draft_payload(type="iceberg", config={"catalog_type": "glue"}, secret=None),
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert adapter.received_secret is None


def test_draft_test_iceberg_glue_catalog_with_no_secret_succeeds(
    client: tuple[TestClient, FakeSecretStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end against the REAL `IcebergConnectionAdapter` (only the
    network-touching `pyiceberg.catalog.load_catalog` call mocked out, the
    same pattern `test_iceberg.py` uses) — a Glue catalog needs no
    credential, so a draft test with `secret=None` must pass."""
    api, _ = client

    class _FakeCatalog:
        def list_namespaces(self) -> list[str]:
            return []

    def fake_load_catalog(name: str, **props: Any) -> _FakeCatalog:
        # No credential was configured, so nothing should have been injected.
        assert "token" not in props
        return _FakeCatalog()

    monkeypatch.setattr("pyiceberg.catalog.load_catalog", fake_load_catalog)
    resp = api.post(
        "/api/v1/connections/test",
        json={"type": "iceberg", "env": "dev", "config": {"catalog_type": "glue"}, "secret": None},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_draft_test_dbt_file_scheme_with_no_secret_succeeds(
    client: tuple[TestClient, FakeSecretStore],
) -> None:
    """End-to-end against the REAL `DbtConnectionAdapter` — a local `file://`
    artifacts path needs no credential (the connection docstring); a
    not-yet-published job is still a green test, so nothing needs to be
    mocked or pre-created on disk."""
    api, _ = client
    resp = api.post(
        "/api/v1/connections/test",
        json={
            "type": "dbt",
            "env": "dev",
            "config": {
                "project_name": "analytics",
                "artifacts_uri": "file:///tmp/does-not-exist-351",
                "jobs": ["nightly"],
            },
            "secret": None,
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_test_endpoint_secret_optional_saved_connection_succeeds(
    client: tuple[TestClient, FakeSecretStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saved-path parity: a connection saved with NO credential (a legitimate
    credential-less catalog) must still test green via `/connections/{id}/test`,
    not 502 "connection has no stored credential to test with"."""
    api, _ = client
    created = api.post(
        "/api/v1/connections",
        json={
            "name": "iceberg-glue",
            "type": "iceberg",
            "env": "dev",
            "config": {"catalog_type": "glue"},
        },
    )
    assert created.status_code == 201
    assert created.json()["has_secret"] is False
    cid = created.json()["id"]

    class _FakeCatalog:
        def list_namespaces(self) -> list[str]:
            return []

    monkeypatch.setattr("pyiceberg.catalog.load_catalog", lambda name, **props: _FakeCatalog())
    resp = api.post(f"/api/v1/connections/{cid}/test")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ────────── a SECOND credential — the Iceberg catalog secret (#1181) ─────────

_ICEBERG_SQL_CONFIG = {"catalog_type": "sql", "catalog_uri": "sqlite:///w"}


def test_create_iceberg_with_catalog_secret_hides_both_secrets(
    client: tuple[TestClient, FakeSecretStore],
) -> None:
    api, store = client
    resp = api.post(
        "/api/v1/connections",
        json={
            "name": "harness-iceberg",
            "type": "iceberg",
            "env": "dev",
            "config": dict(_ICEBERG_SQL_CONFIG),
            "secret": "storage-key",
            "catalog_secret": "catalog-db-pw",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "catalog_secret" not in body
    assert "catalog-db-pw" not in str(body)
    assert "storage-key" not in str(body)
    ref = body["config"]["catalog_secret_name"]
    assert ref != body["config"].get("secret_ref")  # distinct from the storage credential
    assert store.data[ref] == "catalog-db-pw"


def test_create_catalog_secret_unsupported_type_returns_422(
    client: tuple[TestClient, FakeSecretStore],
) -> None:
    """Snowflake's config model has no `catalog_secret_name` field to point at —
    the write must be rejected before anything is persisted."""
    api, store = client
    resp = api.post("/api/v1/connections", json=_create_payload(catalog_secret="nope"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "connection_config_invalid"
    assert store.data == {}


def test_patch_rotates_catalog_secret_reusing_the_same_ref(
    client: tuple[TestClient, FakeSecretStore],
) -> None:
    api, store = client
    created = api.post(
        "/api/v1/connections",
        json={
            "name": "harness-iceberg",
            "type": "iceberg",
            "env": "dev",
            "config": dict(_ICEBERG_SQL_CONFIG),
            "catalog_secret": "pw-v1",
        },
    )
    assert created.status_code == 201
    cid = created.json()["id"]
    ref = created.json()["config"]["catalog_secret_name"]

    resp = api.patch(f"/api/v1/connections/{cid}", json={"catalog_secret": "pw-v2"})
    assert resp.status_code == 200
    assert resp.json()["config"]["catalog_secret_name"] == ref
    assert store.data[ref] == "pw-v2"


def test_draft_test_iceberg_sql_catalog_with_catalog_secret_injects_password(
    client: tuple[TestClient, FakeSecretStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end against the REAL `IcebergConnectionAdapter` (only
    `pyiceberg.catalog.load_catalog` mocked out): a draft's `catalog_secret` —
    the raw value, since nothing is stored yet to name via `_secret_name` — must
    reach the adapter and get injected into the catalog URI's userinfo."""
    api, _ = client
    captured: dict[str, Any] = {}

    class _FakeCatalog:
        def list_namespaces(self) -> list[str]:
            return []

    def fake_load_catalog(name: str, **props: Any) -> _FakeCatalog:
        captured.update(props)
        return _FakeCatalog()

    monkeypatch.setattr("pyiceberg.catalog.load_catalog", fake_load_catalog)
    resp = api.post(
        "/api/v1/connections/test",
        json={
            "type": "iceberg",
            "env": "dev",
            "config": {
                "catalog_type": "sql",
                "catalog_uri": "postgresql://catalog_user@localhost:5432/catalog",
            },
            "secret": None,
            "catalog_secret": "db-pw",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert captured["uri"] == "postgresql://catalog_user:db-pw@localhost:5432/catalog"
    assert "db-pw" not in resp.text


def test_draft_test_unknown_type_returns_422(client: tuple[TestClient, FakeSecretStore]) -> None:
    api, _ = client
    resp = api.post("/api/v1/connections/test", json=_draft_payload(type="mssql"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "connection_config_invalid"


def test_draft_test_invalid_config_returns_422(client: tuple[TestClient, FakeSecretStore]) -> None:
    api, _ = client
    bad = {k: v for k, v in _SF_CONFIG.items() if k != "account"}
    resp = api.post("/api/v1/connections/test", json=_draft_payload(config=bad))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "connection_config_invalid"


def test_draft_test_env_is_optional(
    client: tuple[TestClient, FakeSecretStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    # env plays no role in the probe itself — a caller that hasn't picked one
    # yet still gets a full connectivity check, not a 422.
    api, _ = client
    monkeypatch.setattr(svc, "get_connection_adapter", lambda t: _PassAdapter())
    payload = _draft_payload()
    del payload["env"]
    resp = api.post("/api/v1/connections/test", json=payload)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_draft_test_invalid_env_returns_422(client: tuple[TestClient, FakeSecretStore]) -> None:
    api, _ = client
    resp = api.post("/api/v1/connections/test", json=_draft_payload(env="staging"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "connection_config_invalid"


def test_draft_test_persists_nothing(
    client: tuple[TestClient, FakeSecretStore], db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of the endpoint: no `connections` row, no SecretStore
    write — a failed OR a successful probe must leave both untouched."""
    api, store = client
    monkeypatch.setattr(svc, "get_connection_adapter", lambda t: _PassAdapter())
    ok = api.post("/api/v1/connections/test", json=_draft_payload())
    assert ok.status_code == 200

    monkeypatch.setattr(svc, "get_connection_adapter", lambda t: _FailAdapter())
    failed = api.post("/api/v1/connections/test", json=_draft_payload())
    assert failed.status_code == 502

    assert db_session.scalars(select(Connection)).all() == []
    assert store.data == {}


def test_draft_test_requires_auth(db_session: Any) -> None:
    store = FakeSecretStore()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_secret_store] = lambda: store

    def _reject() -> None:
        raise HTTPException(status_code=401, detail="unauthorized")

    app.dependency_overrides[get_current_user] = _reject
    try:
        resp = TestClient(app).post("/api/v1/connections/test", json=_draft_payload())
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_both_test_routes_resolve(
    client: tuple[TestClient, FakeSecretStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The static `/connections/test` and the parameterized
    `/connections/{connection_id}/test` are different path shapes (two segments
    vs three) — this pins down that both resolve to their own handler rather
    than one shadowing the other."""
    api, _ = client
    monkeypatch.setattr(svc, "get_connection_adapter", lambda t: _PassAdapter())
    cid = api.post("/api/v1/connections", json=_create_payload()).json()["id"]

    saved = api.post(f"/api/v1/connections/{cid}/test")
    assert saved.status_code == 200
    assert saved.json() == {"ok": True}

    draft = api.post("/api/v1/connections/test", json=_draft_payload())
    assert draft.status_code == 200
    assert draft.json() == {"ok": True}


# ───────────────────────── auth gating ─────────────────────────────


def test_create_requires_auth(db_session: Any) -> None:
    store = FakeSecretStore()
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


# ───────────────────────── version history ─────────────────────────


def test_list_versions_returns_history_newest_first(
    client: tuple[TestClient, FakeSecretStore],
) -> None:
    api, _ = client
    cid = api.post("/api/v1/connections", json=_create_payload()).json()["id"]
    api.patch(f"/api/v1/connections/{cid}", json={"name": "renamed"})

    resp = api.get(f"/api/v1/connections/{cid}/versions")
    assert resp.status_code == 200
    body = resp.json()
    assert [v["version_no"] for v in body] == [2, 1]
    assert body[0]["name"] == "renamed"
    assert body[0]["changed_by_name"] is not None  # the dev-bypass author
    assert all("secret" not in v for v in body)  # credential never surfaced


def test_list_versions_unknown_connection_404(client: tuple[TestClient, FakeSecretStore]) -> None:
    api, _ = client
    resp = api.get(f"/api/v1/connections/{uuid.uuid4()}/versions")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "connection_not_found"


def test_list_versions_requires_auth(db_session: Any) -> None:
    app.dependency_overrides[get_db] = lambda: db_session

    def _reject() -> None:
        raise HTTPException(status_code=401, detail="unauthorized")

    app.dependency_overrides[get_current_user] = _reject
    try:
        resp = TestClient(app).get(f"/api/v1/connections/{uuid.uuid4()}/versions")
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


# ─────────────────── credential expiry on the read API (#838) ────────────────


_ADLS_SAS = "sv=2022-11-02&ss=b&sp=rl&se=2026-07-29T05:59:59Z&sig=notarealsignature%3D"


def test_credential_expiry_is_served_on_create_and_list(
    client: tuple[TestClient, FakeSecretStore],
) -> None:
    """The date the UI badges on has to actually cross the API.

    Everything upstream of this can be right — the SAS parsed, the column
    written — and the warning still never reaches anyone if the response model
    drops the field. That is the whole delivery path for #838's user-visible half.
    """
    api, _ = client
    payload = {
        "name": "adls-lake",
        "type": "adls_gen2",
        "env": "dev",
        "config": {"account_url": "https://acct.blob.core.windows.net", "container": "data"},
        "secret": _ADLS_SAS,
    }
    created = api.post("/api/v1/connections", json=payload)
    assert created.status_code == 201
    assert created.json()["credential_expires_at"].startswith("2026-07-29T05:59:59")

    listed = api.get("/api/v1/connections", params={"type": "adls_gen2"})
    assert listed.json()[0]["credential_expires_at"].startswith("2026-07-29T05:59:59")


def test_a_credential_with_no_stated_expiry_serves_null_not_a_guess(
    client: tuple[TestClient, FakeSecretStore],
) -> None:
    # NULL is "unknown", and the UI renders unknown as silence. A fabricated date
    # here would be a reassurance the credential never gave us.
    api, _ = client
    resp = api.post("/api/v1/connections", json=_create_payload())
    assert resp.status_code == 201
    assert resp.json()["credential_expires_at"] is None
