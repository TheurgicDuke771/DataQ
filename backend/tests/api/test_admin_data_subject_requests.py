"""Data-subject-rights endpoint tests (G2, #432)."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.db.models import (
    AuditEvent,
    Check,
    Connection,
    Incident,
    Result,
    Run,
    Suite,
    User,
)
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import suite_service


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _seed_result(db_session: Any, *, sample: dict[str, Any]) -> Result:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:6]}@example.com")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "a"},
        secret_ref="kv-sf",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(name="s", connection_id=conn.id, created_by=owner.id, target={"table": "T"})
    db_session.add(suite)
    db_session.flush()
    check = Check(suite_id=suite.id, name="c", expectation_type="expect_x", config={})
    db_session.add(check)
    db_session.flush()
    run = Run(suite_id=suite.id, status="succeeded")
    db_session.add(run)
    db_session.flush()
    result = Result(run_id=run.id, check_id=check.id, status="fail", sample_failures=sample)
    db_session.add(result)
    db_session.commit()
    return result


def test_non_admin_gets_403(client: TestClient, as_role: Any) -> None:
    _, headers = as_role("member")
    resp = client.post(
        "/api/v1/admin/data-subject-requests/export",
        json={"column": "email", "value": "alice@example.com"},
        headers=headers,
    )
    assert resp.status_code == 403
    resp = client.post(
        "/api/v1/admin/data-subject-requests/erase",
        json={"column": "email", "value": "alice@example.com"},
        headers=headers,
    )
    assert resp.status_code == 403


def test_export_returns_matches_unredacted(
    client: TestClient, db_session: Any, as_role: Any
) -> None:
    result = _seed_result(
        db_session,
        sample={"unexpected_index_list": [{"email": "alice@example.com", "id": 1}]},
    )
    _, headers = as_role("admin")

    resp = client.post(
        "/api/v1/admin/data-subject-requests/export",
        json={"column": "email", "value": "alice@example.com"},
        headers=headers,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["match_count"] == 1
    assert body["matches"][0]["result_id"] == str(result.id)
    assert body["matches"][0]["sample_failures"] == {
        "unexpected_index_list": [{"email": "alice@example.com", "id": 1}]
    }


def test_export_records_access_event(client: TestClient, db_session: Any, as_role: Any) -> None:
    _seed_result(db_session, sample={"unexpected_index_list": [{"email": "alice@example.com"}]})
    _, headers = as_role("admin")

    client.post(
        "/api/v1/admin/data-subject-requests/export",
        json={"column": "email", "value": "alice@example.com"},
        headers=headers,
    )

    event = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "data_subject_request.export")
    ).one()
    assert event.action_class == "access"
    assert event.after["exposed"] is True


def test_erase_scrubs_the_match_and_records_a_config_event(
    client: TestClient, db_session: Any, as_role: Any
) -> None:
    result = _seed_result(
        db_session,
        sample={
            "unexpected_index_list": [
                {"email": "alice@example.com", "id": 1},
                {"email": "bob@example.com", "id": 2},
            ]
        },
    )
    _, headers = as_role("admin")

    resp = client.post(
        "/api/v1/admin/data-subject-requests/erase",
        json={"column": "email", "value": "alice@example.com"},
        headers=headers,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["matched_count"] == 1
    assert body["erased_count"] == 1

    db_session.refresh(result)
    assert result.sample_failures == {
        "unexpected_index_list": [{"email": "bob@example.com", "id": 2}]
    }

    event = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "data_subject_request.erase")
    ).one()
    assert event.action_class == "config"
    assert event.after["erased_count"] == 1


def test_erase_with_no_match_is_a_no_op(client: TestClient, db_session: Any, as_role: Any) -> None:
    _seed_result(db_session, sample={"unexpected_index_list": [{"email": "nobody@x.com"}]})
    _, headers = as_role("admin")

    resp = client.post(
        "/api/v1/admin/data-subject-requests/erase",
        json={"column": "email", "value": "alice@example.com"},
        headers=headers,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "column": "email",
        "value": "alice@example.com",
        "matched_count": 0,
        "erased_count": 0,
        "matched_result_count": 0,
        "erased_result_count": 0,
        "matched_incident_count": 0,
        "erased_incident_count": 0,
    }


def _seed_incident(db_session: Any, *, observed: dict[str, Any]) -> Incident:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:6]}@example.com")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "a", "database": "RETAIL", "schema": "PUBLIC", "warehouse": "WH"},
        secret_ref="kv-sf",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = suite_service.create_suite(
        db_session,
        name=f"suite-{uuid.uuid4().hex[:6]}",
        description=None,
        connection_id=conn.id,
        created_by=owner.id,
        target={"table": "ORDERS"},
    )
    check = Check(
        suite_id=suite.id, name="c", expectation_type="expect_x", config={"column": "email"}
    )
    db_session.add(check)
    db_session.flush()
    incident = Incident(
        asset_id=suite.asset_id,
        check_id=check.id,
        suite_id=suite.id,
        status="open",
        evidence={"failing_result": {"status": "fail", "observed_value": observed}},
    )
    db_session.add(incident)
    db_session.commit()
    return incident


def test_export_and_erase_reach_an_incident_evidence_snapshot(
    client: TestClient, db_session: Any, as_role: Any
) -> None:
    """#1795: the snapshot is a third persisted copy outside the retention clock."""
    result = _seed_result(
        db_session, sample={"unexpected_index_list": [{"email": "alice@example.com"}]}
    )
    incident = _seed_incident(
        db_session, observed={"observed_value": ["alice@example.com", "x@y.z"]}
    )
    _, headers = as_role("admin")
    payload = {"column": "email", "value": "alice@example.com"}

    body = client.post(
        "/api/v1/admin/data-subject-requests/export", json=payload, headers=headers
    ).json()
    assert body["match_count"] == 2 and body["incident_match_count"] == 1
    assert [m["result_id"] for m in body["matches"]] == [str(result.id)]
    hit = body["incident_matches"][0]
    assert hit["incident_id"] == str(incident.id) and hit["status"] == "open"
    assert hit["observed_value"] == {"observed_value": ["alice@example.com", "x@y.z"]}
    access = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "data_subject_request.export")
    ).one()
    assert access.after["incident_match_count"] == 1 and access.after["match_count"] == 2

    body = client.post(
        "/api/v1/admin/data-subject-requests/erase", json=payload, headers=headers
    ).json()
    assert body["matched_count"] == 2 and body["erased_count"] == 2
    assert body["matched_incident_count"] == 1 and body["erased_incident_count"] == 1
    assert body["matched_result_count"] == 1 and body["erased_result_count"] == 1
    db_session.refresh(incident)
    assert incident.evidence is not None
    assert incident.evidence["failing_result"]["observed_value"] == {"observed_value": ["x@y.z"]}
    erase = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "data_subject_request.erase")
    ).one()
    assert erase.after["erased_incident_count"] == 1 and erase.after["erased_count"] == 2
