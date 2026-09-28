"""`GET /connections/{id}/browse/{catalog,files}` (#466) — gates, bounds and disclosure."""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.core.secret_names import connection_secret_ref
from backend.app.datasources import flatfile
from backend.app.db.models import Connection, User
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import browse_service
from backend.tests.support.fake_secret_store import FakeSecretStore, override_secret_store

_CREDENTIAL = "dapi-stored-credential-never-echoed"
_CONFIGS: dict[str, dict[str, Any]] = {
    "unity_catalog": {"workspace_url": "https://dbc-1.cloud.databricks.com", "warehouse_id": "w"},
    "s3": {"bucket": "landing", "region": "us-east-1", "access_key_id": "AKIAEXAMPLE"},
    "adls_gen2": {"account_url": "https://acct.blob.core.windows.net", "container": "raw"},
    "snowflake": {"account": "ab1", "user": "u", "database": "D", "warehouse": "W"},
}


class _Result:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[Any, ...]]:
        return self._rows


class _FakeConn:
    def __init__(self) -> None:
        self.rows: list[tuple[Any, ...]] = [("dataq_retail",), ("workspace",)]

    def execute(self, clause: Any, params: dict[str, Any] | None = None) -> _Result:
        return _Result(self.rows)


@pytest.fixture
def secret_store() -> FakeSecretStore:
    return FakeSecretStore()


@pytest.fixture
def fake_sql(monkeypatch: pytest.MonkeyPatch) -> _FakeConn:
    fake = _FakeConn()

    @contextlib.contextmanager
    def _open(connection: Connection, store: Any) -> Iterator[_FakeConn]:
        # The route must hand the service the STORED credential, resolved server-side.
        assert store.get(connection.secret_ref) == _CREDENTIAL
        yield fake

    monkeypatch.setattr(browse_service, "_open_connection", _open)
    return fake


@pytest.fixture
def fake_store_listing(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def _list(**kwargs: Any) -> flatfile.DirectoryListing:
        seen.update(kwargs)
        return flatfile.DirectoryListing(
            folders=["raw/2026/"],
            files=[flatfile.BrowseFile(path="raw/a.csv", size=3, last_modified=None)],
            truncated=False,
        )

    monkeypatch.setattr(flatfile, "list_directory", _list)
    return seen


@pytest.fixture
def client(db_session: Any, secret_store: FakeSecretStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    override_secret_store(app, secret_store)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _connection(
    db_session: Any, owner: User, secret_store: FakeSecretStore, conn_type: str
) -> Connection:
    conn = Connection(
        id=uuid.uuid4(),
        name=f"conn-{uuid.uuid4().hex[:8]}",
        type=conn_type,
        env="dev",
        config=dict(_CONFIGS[conn_type]),
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.commit()
    conn.secret_ref = connection_secret_ref(
        connection_id=conn.id, env=conn.env, name=conn.name, conn_type=conn.type
    )
    secret_store.set(conn.secret_ref, _CREDENTIAL)
    db_session.commit()
    return conn


@pytest.mark.parametrize("role", ["viewer", "member", "admin"])
def test_catalog_browse_is_member_plus(
    client: TestClient,
    db_session: Any,
    as_role: Any,
    secret_store: FakeSecretStore,
    fake_sql: _FakeConn,
    role: str,
) -> None:
    """Member+, like `/test` and suite creation: a Viewer can author no suite, and the
    listing opens an outbound connection with the stored credential.
    """
    actor, headers = as_role(role)
    conn = _connection(db_session, actor, secret_store, "unity_catalog")
    resp = client.get(f"/api/v1/connections/{conn.id}/browse/catalog", headers=headers)
    assert resp.status_code == (403 if role == "viewer" else 200)


@pytest.mark.parametrize("role", ["viewer", "member", "admin"])
def test_file_browse_is_member_plus(
    client: TestClient,
    db_session: Any,
    as_role: Any,
    secret_store: FakeSecretStore,
    fake_store_listing: dict[str, Any],
    role: str,
) -> None:
    actor, headers = as_role(role)
    conn = _connection(db_session, actor, secret_store, "s3")
    resp = client.get(f"/api/v1/connections/{conn.id}/browse/files", headers=headers)
    assert resp.status_code == (403 if role == "viewer" else 200)


def test_catalog_browse_response_shape_and_no_credential(
    client: TestClient,
    db_session: Any,
    as_role: Any,
    secret_store: FakeSecretStore,
    fake_sql: _FakeConn,
) -> None:
    actor, headers = as_role("member")
    conn = _connection(db_session, actor, secret_store, "unity_catalog")
    fake_sql.rows = [
        ("dataq_retail", "gold", "daily_revenue", "MANAGED"),
        ("dataq_retail", "gold", "bad-name", "VIEW"),
        ("dataq_retail", "gold", "zeta", "MANAGED"),
    ]
    resp = client.get(
        f"/api/v1/connections/{conn.id}/browse/catalog",
        params={"catalog": "dataq_retail", "schema": "gold", "limit": 2},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "level": "table",
        "catalog": "dataq_retail",
        "schema": "gold",
        "entries": [
            {"name": "daily_revenue", "selectable": True, "object_type": "table"},
            {"name": "bad-name", "selectable": False, "object_type": "view"},
        ],
        "truncated": True,
        "limit": 2,
    }
    assert _CREDENTIAL not in resp.text


def test_file_browse_response_shape_and_no_credential(
    client: TestClient,
    db_session: Any,
    as_role: Any,
    secret_store: FakeSecretStore,
    fake_store_listing: dict[str, Any],
) -> None:
    actor, headers = as_role("member")
    conn = _connection(db_session, actor, secret_store, "adls_gen2")
    resp = client.get(
        f"/api/v1/connections/{conn.id}/browse/files",
        params={"prefix": "raw/", "limit": 25},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "root": "raw",
        "prefix": "raw/",
        "folders": ["raw/2026/"],
        "files": [{"path": "raw/a.csv", "size": 3, "last_modified": None}],
        "truncated": False,
        "limit": 25,
    }
    assert fake_store_listing["limit"] == 25 and fake_store_listing["prefix"] == "raw/"
    assert _CREDENTIAL not in resp.text


@pytest.mark.parametrize("limit", [0, -1, 501, 10_000])
def test_the_limit_is_bounded(
    client: TestClient,
    db_session: Any,
    as_role: Any,
    secret_store: FakeSecretStore,
    fake_sql: _FakeConn,
    limit: int,
) -> None:
    actor, headers = as_role("member")
    conn = _connection(db_session, actor, secret_store, "unity_catalog")
    resp = client.get(
        f"/api/v1/connections/{conn.id}/browse/catalog", params={"limit": limit}, headers=headers
    )
    assert resp.status_code == 422


def test_an_overlong_prefix_is_refused(
    client: TestClient,
    db_session: Any,
    as_role: Any,
    secret_store: FakeSecretStore,
    fake_store_listing: dict[str, Any],
) -> None:
    actor, headers = as_role("member")
    conn = _connection(db_session, actor, secret_store, "s3")
    resp = client.get(
        f"/api/v1/connections/{conn.id}/browse/files",
        params={"prefix": "a/" * 600},
        headers=headers,
    )
    assert resp.status_code == 422
    assert fake_store_listing == {}


@pytest.mark.parametrize("prefix", ["../", "raw/../x/", "/etc/", "raw\\x/", "raw/\x00/"])
def test_a_traversal_prefix_is_422_and_never_listed(
    client: TestClient,
    db_session: Any,
    as_role: Any,
    secret_store: FakeSecretStore,
    fake_store_listing: dict[str, Any],
    prefix: str,
) -> None:
    actor, headers = as_role("member")
    conn = _connection(db_session, actor, secret_store, "s3")
    resp = client.get(
        f"/api/v1/connections/{conn.id}/browse/files", params={"prefix": prefix}, headers=headers
    )
    assert resp.status_code == 422
    assert fake_store_listing == {}


def test_browsing_the_wrong_kind_of_connection_is_422(
    client: TestClient,
    db_session: Any,
    as_role: Any,
    secret_store: FakeSecretStore,
    fake_sql: _FakeConn,
    fake_store_listing: dict[str, Any],
) -> None:
    actor, headers = as_role("member")
    s3 = _connection(db_session, actor, secret_store, "s3")
    uc = _connection(db_session, actor, secret_store, "unity_catalog")
    catalog = client.get(f"/api/v1/connections/{s3.id}/browse/catalog", headers=headers)
    files = client.get(f"/api/v1/connections/{uc.id}/browse/files", headers=headers)
    assert (catalog.status_code, files.status_code) == (422, 422)
    assert catalog.json()["error"]["code"] == "browse_unsupported"


def test_an_unknown_connection_is_404(client: TestClient, as_role: Any) -> None:
    _, headers = as_role("member")
    resp = client.get(f"/api/v1/connections/{uuid.uuid4()}/browse/catalog", headers=headers)
    assert resp.status_code == 404


def test_a_listing_failure_is_a_502_envelope_without_the_driver_message(
    client: TestClient,
    db_session: Any,
    as_role: Any,
    secret_store: FakeSecretStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(**_kw: Any) -> flatfile.DirectoryListing:
        raise RuntimeError(f"AccessDenied key={_CREDENTIAL}")

    monkeypatch.setattr(flatfile, "list_directory", _boom)
    actor, headers = as_role("member")
    conn = _connection(db_session, actor, secret_store, "s3")
    resp = client.get(f"/api/v1/connections/{conn.id}/browse/files", headers=headers)
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "browse_failed"
    assert _CREDENTIAL not in resp.text
