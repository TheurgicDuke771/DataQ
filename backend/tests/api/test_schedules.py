"""Schedule endpoint tests (TestClient + real Postgres).

Mirrors the trigger-binding tests: get_db is overridden to the shared test
session, auth runs in dev-bypass mode (conftest) so the caller is the dev user,
and suites created via the API are owned by that user. Skips without
TEST_DATABASE_URL.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.db.models import Connection, Schedule, Suite, User
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
    owner = User(aad_object_id=uuid.uuid4().hex, email="owner@example.com")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "ab12345.eu-west-1"},
        secret_ref="kv-sf",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.commit()
    return conn


def _owned_suite(client: TestClient, connection_id: uuid.UUID) -> str:
    resp = client.post(
        "/api/v1/suites",
        json={"name": f"s-{uuid.uuid4().hex[:8]}", "connection_id": str(connection_id)},
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _unowned_suite(db_session: Any, connection: Connection) -> Suite:
    other = User(aad_object_id=uuid.uuid4().hex, email="other@example.com")
    db_session.add(other)
    db_session.flush()
    suite = Suite(name="theirs", connection_id=connection.id, created_by=other.id)
    db_session.add(suite)
    db_session.commit()
    return suite


def _payload(suite_id: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"suite_id": suite_id, "cron": "0 6 * * *", "timezone": "UTC"}
    body.update(overrides)
    return body


def test_create_then_get_schedule(client: TestClient, db_session: Any) -> None:
    suite_id = _owned_suite(client, _connection(db_session).id)
    created = client.post("/api/v1/schedules", json=_payload(suite_id))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["cron"] == "0 6 * * *"
    assert body["enabled"] is True
    assert body["last_run_at"] is None
    # next_run_at is precomputed and in the future
    assert datetime.fromisoformat(body["next_run_at"]) > datetime.now(UTC)

    got = client.get(f"/api/v1/schedules/{body['id']}")
    assert got.status_code == 200
    assert got.json()["suite_id"] == suite_id


def test_create_rejects_bad_cron(client: TestClient, db_session: Any) -> None:
    suite_id = _owned_suite(client, _connection(db_session).id)
    resp = client.post("/api/v1/schedules", json=_payload(suite_id, cron="not a cron"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_cron"
    assert db_session.scalar(select(func.count()).select_from(Schedule)) == 0


def test_create_rejects_impossible_calendar_cron(client: TestClient, db_session: Any) -> None:
    """A syntactically-valid but unsatisfiable cron (Feb 30) is a clean 422, not a
    500 — croniter.is_valid passes it but it never fires."""
    suite_id = _owned_suite(client, _connection(db_session).id)
    resp = client.post("/api/v1/schedules", json=_payload(suite_id, cron="0 0 30 2 *"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_cron"
    assert db_session.scalar(select(func.count()).select_from(Schedule)) == 0


def test_create_rejects_unknown_timezone(client: TestClient, db_session: Any) -> None:
    suite_id = _owned_suite(client, _connection(db_session).id)
    resp = client.post("/api/v1/schedules", json=_payload(suite_id, timezone="Mars/Phobos"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_timezone"


def test_create_on_inaccessible_suite_is_404(client: TestClient, db_session: Any) -> None:
    suite = _unowned_suite(db_session, _connection(db_session))
    resp = client.post("/api/v1/schedules", json=_payload(str(suite.id)))
    assert resp.status_code == 404
    assert db_session.scalar(select(func.count()).select_from(Schedule)) == 0


def test_create_with_view_only_is_forbidden(client: TestClient, db_session: Any) -> None:
    from backend.app.core.auth import DEV_BYPASS_AAD_OID
    from backend.app.db.models import Share

    suite = _unowned_suite(db_session, _connection(db_session))
    client.get("/api/v1/schedules")  # warm up auth so the dev-bypass user row exists
    me = db_session.scalar(select(User).where(User.aad_object_id == DEV_BYPASS_AAD_OID))
    db_session.add(Share(suite_id=suite.id, user_id=me.id, permission="view"))
    db_session.commit()

    resp = client.post("/api/v1/schedules", json=_payload(str(suite.id)))
    assert resp.status_code == 403


def test_list_is_scoped_to_accessible_suites(client: TestClient, db_session: Any) -> None:
    conn = _connection(db_session)
    mine = _owned_suite(client, conn.id)
    client.post("/api/v1/schedules", json=_payload(mine))
    theirs = _unowned_suite(db_session, conn)
    db_session.add(
        Schedule(
            suite_id=theirs.id,
            cron="0 0 * * *",
            timezone="UTC",
            next_run_at=datetime(2030, 1, 1, tzinfo=UTC),
            created_by=theirs.created_by,
        )
    )
    db_session.commit()

    listed = client.get("/api/v1/schedules")
    assert listed.status_code == 200
    assert {s["suite_id"] for s in listed.json()} == {mine}


def test_patch_cron_recomputes_next_run_at(client: TestClient, db_session: Any) -> None:
    suite_id = _owned_suite(client, _connection(db_session).id)
    created = client.post("/api/v1/schedules", json=_payload(suite_id)).json()
    patched = client.patch(f"/api/v1/schedules/{created['id']}", json={"cron": "30 9 * * *"})
    assert patched.status_code == 200
    assert patched.json()["cron"] == "30 9 * * *"
    assert patched.json()["next_run_at"] != created["next_run_at"]


def test_patch_rejects_bad_cron(client: TestClient, db_session: Any) -> None:
    suite_id = _owned_suite(client, _connection(db_session).id)
    sid = client.post("/api/v1/schedules", json=_payload(suite_id)).json()["id"]
    resp = client.patch(f"/api/v1/schedules/{sid}", json={"cron": "* * *"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_cron"


def test_toggle_then_delete(client: TestClient, db_session: Any) -> None:
    suite_id = _owned_suite(client, _connection(db_session).id)
    sid = client.post("/api/v1/schedules", json=_payload(suite_id)).json()["id"]

    disabled = client.patch(f"/api/v1/schedules/{sid}", json={"enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False

    deleted = client.delete(f"/api/v1/schedules/{sid}")
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/schedules/{sid}").status_code == 404
