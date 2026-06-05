"""Check endpoint tests against a real Postgres (db_session) via TestClient.

Checks are nested under a suite. A connection + suite are created per test for
the FK chain; auth runs in dev-bypass (conftest). Skips without
TEST_DATABASE_URL.
"""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.db.models import Connection, User
from backend.app.db.session import get_db
from backend.app.main import app


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _suite_id(client: TestClient, db_session: Any) -> str:
    """Create a connection (ORM) + suite (API) and return the suite id."""
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
    resp = client.post(
        "/api/v1/suites",
        json={"name": "finance", "description": None, "connection_id": str(conn.id)},
    )
    return str(resp.json()["id"])


def _payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "orders not null",
        "expectation_type": "expect_column_values_to_not_be_null",
        "config": {"column": "order_id"},
    }
    body.update(overrides)
    return body


# ───────────────────────── create ──────────────────────────────────


def test_create_returns_201_with_defaults(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    resp = client.post(f"/api/v1/suites/{sid}/checks", json=_payload())
    assert resp.status_code == 201
    body = resp.json()
    assert body["suite_id"] == sid
    assert body["kind"] == "expectation"  # default
    assert body["expectation_type"] == "expect_column_values_to_not_be_null"
    assert body["config"] == {"column": "order_id"}
    assert body["warn_threshold"] is None


def test_create_stores_thresholds_as_numbers(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json=_payload(warn_threshold=0.95, fail_threshold=0.9, critical_threshold=0.5),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["warn_threshold"] == 0.95
    assert body["fail_threshold"] == 0.9
    assert body["critical_threshold"] == 0.5


def test_create_rejects_non_expectation_kind(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    resp = client.post(f"/api/v1/suites/{sid}/checks", json=_payload(kind="freshness"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "check_config_invalid"


def test_create_in_unknown_suite_returns_404(client: TestClient) -> None:
    resp = client.post(f"/api/v1/suites/{uuid.uuid4()}/checks", json=_payload())
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "suite_not_found"


def test_create_blank_name_or_expectation_returns_422(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    blank_name = client.post(f"/api/v1/suites/{sid}/checks", json=_payload(name=""))
    assert blank_name.status_code == 422
    blank_type = client.post(f"/api/v1/suites/{sid}/checks", json=_payload(expectation_type=""))
    assert blank_type.status_code == 422


# ───────────────────────── read / list ─────────────────────────────


def test_list_returns_suite_checks(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    client.post(f"/api/v1/suites/{sid}/checks", json=_payload(name="c1"))
    client.post(f"/api/v1/suites/{sid}/checks", json=_payload(name="c2"))
    resp = client.get(f"/api/v1/suites/{sid}/checks")
    assert resp.status_code == 200
    assert {c["name"] for c in resp.json()} == {"c1", "c2"}


def test_list_empty_suite_returns_empty(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    resp = client.get(f"/api/v1/suites/{sid}/checks")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_returns_check(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload()).json()["id"]
    resp = client.get(f"/api/v1/suites/{sid}/checks/{cid}")
    assert resp.status_code == 200
    assert resp.json()["id"] == cid


def test_get_unknown_check_returns_404(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    resp = client.get(f"/api/v1/suites/{sid}/checks/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "check_not_found"


def test_check_is_scoped_to_its_suite(client: TestClient, db_session: Any) -> None:
    sid_a = _suite_id(client, db_session)
    sid_b = _suite_id(client, db_session)
    cid = client.post(f"/api/v1/suites/{sid_a}/checks", json=_payload()).json()["id"]
    # the check exists, but not under suite B's path
    cross = client.get(f"/api/v1/suites/{sid_b}/checks/{cid}")
    assert cross.status_code == 404
    assert client.get(f"/api/v1/suites/{sid_a}/checks/{cid}").status_code == 200


# ───────────────────────── update / delete ─────────────────────────


def test_patch_updates_fields(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload()).json()["id"]
    resp = client.patch(
        f"/api/v1/suites/{sid}/checks/{cid}",
        json={
            "name": "renamed",
            "expectation_type": "expect_column_values_to_be_unique",
            "config": {"column": "amount"},
            "warn_threshold": 1.5,
            "fail_threshold": 3,
            "critical_threshold": 5,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "renamed"
    assert body["expectation_type"] == "expect_column_values_to_be_unique"
    assert body["config"] == {"column": "amount"}
    assert body["warn_threshold"] == 1.5
    assert body["fail_threshold"] == 3
    assert body["critical_threshold"] == 5


def test_patch_unknown_check_returns_404(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    resp = client.patch(f"/api/v1/suites/{sid}/checks/{uuid.uuid4()}", json={"name": "x"})
    assert resp.status_code == 404


def test_delete_returns_204_then_404(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    cid = client.post(f"/api/v1/suites/{sid}/checks", json=_payload()).json()["id"]
    deleted = client.delete(f"/api/v1/suites/{sid}/checks/{cid}")
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/suites/{sid}/checks/{cid}").status_code == 404
