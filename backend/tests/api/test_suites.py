"""Suite endpoint tests against a real Postgres (db_session) via TestClient.

get_db is overridden to the shared test session; auth runs in dev-bypass mode
(conftest), which upserts the dev user for the suite's created_by. A connection
(with its own owner) is inserted directly for the FK. Skips without
TEST_DATABASE_URL.
"""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.db.models import Check, Connection, Suite, User
from backend.app.db.session import get_db
from backend.app.main import app


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _connection(db_session: Any) -> Connection:
    """Insert a connection (with its own owner) for suites to bind to."""
    owner = User(aad_object_id=uuid.uuid4().hex, email="owner@example.com")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "ab12345.eu-west-1"},
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.commit()
    return conn


def _payload(connection_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "finance-checks",
        "description": "row + null checks for finance tables",
        "connection_id": str(connection_id),
    }
    body.update(overrides)
    return body


# ───────────────────────── create ──────────────────────────────────


def test_create_returns_201(client: TestClient, db_session: Any) -> None:
    conn = _connection(db_session)
    resp = client.post("/api/v1/suites", json=_payload(conn.id))
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "finance-checks"
    assert body["connection_id"] == str(conn.id)
    assert body["created_by"] is not None  # the dev-bypass user
    # persisted
    assert db_session.get(Suite, uuid.UUID(body["id"])) is not None


def test_create_unknown_connection_returns_422(client: TestClient) -> None:
    resp = client.post("/api/v1/suites", json=_payload(uuid.uuid4()))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "suite_connection_invalid"


def test_create_blank_name_returns_422(client: TestClient, db_session: Any) -> None:
    conn = _connection(db_session)
    resp = client.post("/api/v1/suites", json=_payload(conn.id, name=""))
    assert resp.status_code == 422


def test_create_allows_null_description(client: TestClient, db_session: Any) -> None:
    conn = _connection(db_session)
    resp = client.post("/api/v1/suites", json=_payload(conn.id, description=None))
    assert resp.status_code == 201
    assert resp.json()["description"] is None


# ───────────────────────── read / list ─────────────────────────────


def test_get_returns_suite(client: TestClient, db_session: Any) -> None:
    conn = _connection(db_session)
    sid = client.post("/api/v1/suites", json=_payload(conn.id)).json()["id"]
    resp = client.get(f"/api/v1/suites/{sid}")
    assert resp.status_code == 200
    assert resp.json()["id"] == sid


def test_get_unknown_returns_404(client: TestClient) -> None:
    resp = client.get(f"/api/v1/suites/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "suite_not_found"


def test_list_filters_by_connection(client: TestClient, db_session: Any) -> None:
    conn_a = _connection(db_session)
    conn_b = _connection(db_session)
    client.post("/api/v1/suites", json=_payload(conn_a.id, name="a1"))
    client.post("/api/v1/suites", json=_payload(conn_a.id, name="a2"))
    client.post("/api/v1/suites", json=_payload(conn_b.id, name="b1"))

    all_a = client.get(f"/api/v1/suites?connection_id={conn_a.id}").json()
    assert {s["name"] for s in all_a} == {"a1", "a2"}
    assert len(client.get("/api/v1/suites").json()) == 3  # unfiltered


# ───────────────────────── update / delete ─────────────────────────


def test_patch_updates_name_and_description(client: TestClient, db_session: Any) -> None:
    conn = _connection(db_session)
    sid = client.post("/api/v1/suites", json=_payload(conn.id)).json()["id"]
    resp = client.patch(f"/api/v1/suites/{sid}", json={"name": "renamed", "description": "new"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "renamed"
    assert resp.json()["description"] == "new"


def test_patch_unknown_returns_404(client: TestClient) -> None:
    resp = client.patch(f"/api/v1/suites/{uuid.uuid4()}", json={"name": "x"})
    assert resp.status_code == 404


def test_delete_returns_204_then_404(client: TestClient, db_session: Any) -> None:
    conn = _connection(db_session)
    sid = client.post("/api/v1/suites", json=_payload(conn.id)).json()["id"]
    deleted = client.delete(f"/api/v1/suites/{sid}")
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/suites/{sid}").status_code == 404


def test_delete_cascades_to_checks(client: TestClient, db_session: Any) -> None:
    conn = _connection(db_session)
    sid = client.post("/api/v1/suites", json=_payload(conn.id)).json()["id"]
    # attach a check directly (check CRUD lands in the next PR)
    db_session.add(
        Check(suite_id=uuid.UUID(sid), name="row_count", expectation_type="expect_table_row_count")
    )
    db_session.commit()

    deleted = client.delete(f"/api/v1/suites/{sid}")
    assert deleted.status_code == 204
    remaining = db_session.scalars(select(Check).where(Check.suite_id == uuid.UUID(sid))).all()
    assert remaining == []  # cascade removed the child check
