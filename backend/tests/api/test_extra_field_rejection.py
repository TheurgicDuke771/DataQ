"""Unknown request fields are rejected, not silently dropped (#1505).

`ApiRequestModel` (`extra='forbid'`) replaces `ApiModel`'s default `extra='ignore'`
on every request body. Regression coverage for the issue's own probe (a
`target_override` field on dry-run that never existed and validated cleanly).
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
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"owner-{uuid.uuid4().hex[:8]}@example.com")
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
        json={"name": "extra-field-battery", "connection_id": _connection_id(db_session)},
    )
    assert resp.status_code == 201
    return str(resp.json()["id"])


def _extra_field_names(resp: Any) -> set[str]:
    """The field names pydantic's `extra_forbidden` errors named, from the
    `validation_error` envelope (`core.errors`).
    """
    body = resp.json()
    assert body["error"]["code"] == "validation_error"
    errors = body["error"]["detail"]["errors"]
    return {str(e["loc"][-1]) for e in errors if e.get("type") == "extra_forbidden"}


def _assert_rejects_field(resp: Any, field: str) -> None:
    assert resp.status_code == 422, f"expected 422, got {resp.status_code}: {resp.text}"
    assert field in _extra_field_names(resp), resp.text


# ── representative request bodies (#1505 AC) ─────────────────────────────────


def test_unknown_field_on_create_suite_is_422(client: TestClient, db_session: Any) -> None:
    resp = client.post(
        "/api/v1/suites",
        json={
            "name": "extra-suite",
            "connection_id": _connection_id(db_session),
            "target_override": {"table": "FAKE.TABLE"},
        },
    )
    _assert_rejects_field(resp, "target_override")


def test_unknown_field_on_create_check_is_422(client: TestClient, db_session: Any) -> None:
    sid = _suite_id(client, db_session)
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json={
            "name": "extra-check",
            "expectation_type": "expect_column_values_to_not_be_null",
            "config": {"column": "ID"},
            "target_override": {"table": "FAKE.TABLE"},
        },
    )
    _assert_rejects_field(resp, "target_override")


def test_unknown_field_on_create_connection_is_422(client: TestClient, db_session: Any) -> None:
    resp = client.post(
        "/api/v1/connections",
        json={
            "name": "extra-connection",
            "type": "snowflake",
            "env": "dev",
            "config": {"account": "ab12345.eu-west-1"},
            "target_override": {"table": "FAKE.TABLE"},
        },
    )
    _assert_rejects_field(resp, "target_override")


def test_unknown_field_on_dry_run_is_422(client: TestClient, db_session: Any) -> None:
    """The issue's own probe (#1412, scenario 53): a dry-run body naming a
    `target_override` that never existed validated cleanly and did nothing.
    """
    sid = _suite_id(client, db_session)
    resp = client.post(
        f"/api/v1/suites/{sid}/checks/dryrun",
        json={
            "expectation_type": "expect_column_values_to_not_be_null",
            "config": {"column": "ID"},
            "target_override": {"table": "FAKE.TABLE"},
        },
    )
    _assert_rejects_field(resp, "target_override")


def test_misspelled_threshold_field_is_422(client: TestClient, db_session: Any) -> None:
    """A plausible typo — not a made-up field — must 422 by the same rule."""
    sid = _suite_id(client, db_session)
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json={
            "name": "typo-check",
            "expectation_type": "expect_column_values_to_not_be_null",
            "config": {"column": "ID"},
            "warn_treshold": 5,
        },
    )
    _assert_rejects_field(resp, "warn_treshold")


def test_unknown_key_in_nested_suite_target_is_422(client: TestClient, db_session: Any) -> None:
    """`SuiteTarget`, nested inside `SuiteCreate`, is a request-only sub-model too
    (#1505 AC 4) — `forbid` on the parent alone would not cover it.
    """
    resp = client.post(
        "/api/v1/suites",
        json={
            "name": "nested-extra",
            "connection_id": _connection_id(db_session),
            "target": {"table": "ORDERS", "made_up_key": "x"},
        },
    )
    _assert_rejects_field(resp, "made_up_key")


def test_unknown_key_in_import_document_check_is_422(client: TestClient, db_session: Any) -> None:
    """`CheckDocumentIn`, nested inside the import payload's `document.checks`,
    rejects an unknown key too. The export-side `CheckDocument`/`SuiteDocument`
    is a separate, still-lenient class precisely so `GET /export`'s response
    construction is untouched by this.
    """
    resp = client.post(
        "/api/v1/suites/import",
        json={
            "connection_id": _connection_id(db_session),
            "document": {
                "name": "imported",
                "checks": [
                    {
                        "name": "smuggled",
                        "expectation_type": "expect_column_values_to_not_be_null",
                        "config": {"column": "ID"},
                        "target_override": "x",
                    }
                ],
            },
        },
    )
    _assert_rejects_field(resp, "target_override")


def test_known_fields_still_validate_and_create(client: TestClient, db_session: Any) -> None:
    """Positive control: a clean payload with only known fields is unaffected."""
    resp = client.post(
        "/api/v1/suites",
        json={"name": "clean-suite", "connection_id": _connection_id(db_session)},
    )
    assert resp.status_code == 201


def test_export_response_is_unaffected_by_forbid(client: TestClient, db_session: Any) -> None:
    """`SuiteDocument`/`CheckDocument` (the `GET /export` response shape) stayed
    on `ApiModel` — `export_suite` always builds them from a closed field set, so
    round-tripping still works.
    """
    sid = _suite_id(client, db_session)
    resp = client.post(
        f"/api/v1/suites/{sid}/checks",
        json={
            "name": "exported-check",
            "expectation_type": "expect_column_values_to_not_be_null",
            "config": {"column": "ID"},
        },
    )
    assert resp.status_code == 201
    resp = client.get(f"/api/v1/suites/{sid}/export")
    assert resp.status_code == 200
    assert resp.json()["checks"][0]["name"] == "exported-check"
