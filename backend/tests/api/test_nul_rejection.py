"""NUL-byte rejection at the API boundary (#567) — TestClient over real Postgres.

Pydantic's `str` accepts NUL (``\\x00``) but Postgres rejects it at INSERT for
both text and JSONB, so before `ApiModel` these payloads escaped validation and
died as driver ``ValueError`` → HTTP 500. The contract under test: **NUL
anywhere in a request payload — top-level field, nested config value, dict key,
list item, import document — is a structured 422**, and the same probes without
NUL still succeed (the guard rejects the byte, not the shape). Skips without
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


def _connection_id(db_session: Any) -> str:
    owner = User(aad_object_id=uuid.uuid4().hex, email="owner@example.com")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"snowflake-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "ab12345.eu-west-1"},
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.commit()
    return str(conn.id)


def _suite_id(client: TestClient, db_session: Any) -> str:
    resp = client.post(
        "/api/v1/suites",
        json={"name": "nul-battery", "connection_id": _connection_id(db_session)},
    )
    assert resp.status_code == 201
    return str(resp.json()["id"])


def _assert_422_validation(resp: Any) -> None:
    assert resp.status_code == 422, f"expected 422, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["error"]["code"] == "validation_error"
    assert "NUL" in str(body["error"]["detail"])


def test_nul_in_suite_name_is_422(client: TestClient, db_session: Any) -> None:
    resp = client.post(
        "/api/v1/suites",
        json={"name": "evil-\x00-suite", "connection_id": _connection_id(db_session)},
    )
    _assert_422_validation(resp)


def test_nul_in_suite_description_is_422(client: TestClient, db_session: Any) -> None:
    resp = client.post(
        "/api/v1/suites",
        json={
            "name": "fine",
            "description": "bad\x00desc",
            "connection_id": _connection_id(db_session),
        },
    )
    _assert_422_validation(resp)


def test_nul_in_check_name_is_422(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json={
            "name": "nul\x00check",
            "expectation_type": "expect_column_values_to_not_be_null",
            "config": {"column": "ID"},
        },
    )
    _assert_422_validation(resp)


def test_nul_nested_in_check_config_value_is_422(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json={
            "name": "fine",
            "expectation_type": "expect_column_values_to_be_in_set",
            "config": {"column": "STATUS", "value_set": ["ok", "bad\x00value"]},
        },
    )
    _assert_422_validation(resp)


def test_nul_in_check_config_dict_key_is_422(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json={
            "name": "fine",
            "expectation_type": "expect_column_values_to_not_be_null",
            "config": {"col\x00umn": "ID"},
        },
    )
    _assert_422_validation(resp)


def test_nul_deep_in_import_document_is_422(client: TestClient, db_session: Any) -> None:
    resp = client.post(
        "/api/v1/suites/import",
        json={
            "connection_id": _connection_id(db_session),
            "suite": {
                "name": "imported",
                "checks": [
                    {
                        "name": "smuggled\x00",
                        "expectation_type": "expect_column_values_to_not_be_null",
                        "config": {"column": "ID"},
                    }
                ],
            },
        },
    )
    _assert_422_validation(resp)


def test_nul_in_update_payload_is_422(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    resp = client.patch(f"/api/v1/suites/{sid}", json={"name": "renamed\x00"})
    _assert_422_validation(resp)


def test_nul_free_unicode_still_accepted(client: TestClient, db_session: Any) -> None:
    """The guard rejects the NUL byte, not exotic-but-legit Unicode (control
    chars, RTL override, emoji stay accepted — 1b in the qa-verifier battery)."""
    resp = client.post(
        "/api/v1/suites",
        json={"name": "ok ‮ 🎯 suite", "connection_id": _connection_id(db_session)},
    )
    assert resp.status_code == 201
    sid = resp.json()["id"]
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json={
            "name": "plain",
            "expectation_type": "expect_column_values_to_not_be_null",
            "config": {"column": "ID"},
        },
    )
    assert resp.status_code == 201
