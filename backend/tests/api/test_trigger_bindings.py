"""Trigger-binding endpoint tests (TestClient + real Postgres).

get_db is overridden to the shared test session; auth runs in dev-bypass mode
(conftest) so the caller is the dev user. Suites created via the API are owned by
that user (edit allowed); a directly-inserted suite with a different owner is
used to exercise the access-control paths. Skips without TEST_DATABASE_URL.
"""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.db.models import Connection, Suite, TriggerBinding, User
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
    """Create a suite via the API so it's owned by the dev-bypass caller."""
    resp = client.post(
        "/api/v1/suites",
        json={"name": f"s-{uuid.uuid4().hex[:8]}", "connection_id": str(connection_id)},
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _unowned_suite(db_session: Any, connection: Connection) -> Suite:
    """A suite owned by someone else, not shared with the caller → no access."""
    other = User(aad_object_id=uuid.uuid4().hex, email="other@example.com")
    db_session.add(other)
    db_session.flush()
    suite = Suite(name="theirs", connection_id=connection.id, created_by=other.id)
    db_session.add(suite)
    db_session.commit()
    return suite


def _payload(suite_id: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "provider": "adf",
        "pipeline_or_dag_id": "load_finance",
        "env": "dev",
        "suite_id": suite_id,
    }
    body.update(overrides)
    return body


def test_create_then_get_binding(client: TestClient, db_session: Any) -> None:
    suite_id = _owned_suite(client, _connection(db_session).id)
    created = client.post("/api/v1/trigger-bindings", json=_payload(suite_id))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["provider"] == "adf"
    assert body["enabled"] is True

    got = client.get(f"/api/v1/trigger-bindings/{body['id']}")
    assert got.status_code == 200
    assert got.json()["suite_id"] == suite_id


def test_create_rejects_unknown_provider(client: TestClient, db_session: Any) -> None:
    suite_id = _owned_suite(client, _connection(db_session).id)
    resp = client.post("/api/v1/trigger-bindings", json=_payload(suite_id, provider="prefect"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "trigger_binding_invalid"


def test_duplicate_binding_conflicts(client: TestClient, db_session: Any) -> None:
    suite_id = _owned_suite(client, _connection(db_session).id)
    first = client.post("/api/v1/trigger-bindings", json=_payload(suite_id))
    assert first.status_code == 201
    dup = client.post("/api/v1/trigger-bindings", json=_payload(suite_id))
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "trigger_binding_conflict"


def test_create_on_inaccessible_suite_is_404(client: TestClient, db_session: Any) -> None:
    # A suite the caller has no access to is hidden (404), not 403 — and no row.
    conn = _connection(db_session)
    suite = _unowned_suite(db_session, conn)
    resp = client.post("/api/v1/trigger-bindings", json=_payload(str(suite.id)))
    assert resp.status_code == 404
    assert db_session.scalar(select(func.count()).select_from(TriggerBinding)) == 0


def test_create_with_view_only_is_forbidden(client: TestClient, db_session: Any) -> None:
    from backend.app.core.auth import DEV_BYPASS_AAD_OID
    from backend.app.db.models import Share

    conn = _connection(db_session)
    suite = _unowned_suite(db_session, conn)
    # warm up auth so the dev-bypass user row exists, then share at view-only
    client.get("/api/v1/trigger-bindings")
    me = db_session.scalar(select(User).where(User.aad_object_id == DEV_BYPASS_AAD_OID))
    db_session.add(Share(suite_id=suite.id, user_id=me.id, permission="view"))
    db_session.commit()

    # creating a binding needs `edit`; view-only → 403 (access exists, too low)
    resp = client.post("/api/v1/trigger-bindings", json=_payload(str(suite.id)))
    assert resp.status_code == 403


def test_create_on_missing_suite_404(client: TestClient) -> None:
    resp = client.post("/api/v1/trigger-bindings", json=_payload(str(uuid.uuid4())))
    assert resp.status_code == 404


def test_list_is_scoped_to_accessible_suites(client: TestClient, db_session: Any) -> None:
    conn = _connection(db_session)
    mine = _owned_suite(client, conn.id)
    client.post("/api/v1/trigger-bindings", json=_payload(mine))
    # a binding on a suite I don't own (inserted directly) must not show
    theirs = _unowned_suite(db_session, conn)
    db_session.add(
        TriggerBinding(provider="adf", pipeline_or_dag_id="other", env="dev", suite_id=theirs.id)
    )
    db_session.commit()

    listed = client.get("/api/v1/trigger-bindings")
    assert listed.status_code == 200
    suite_ids = {b["suite_id"] for b in listed.json()}
    assert suite_ids == {mine}


def test_toggle_then_delete(client: TestClient, db_session: Any) -> None:
    suite_id = _owned_suite(client, _connection(db_session).id)
    bid = client.post("/api/v1/trigger-bindings", json=_payload(suite_id)).json()["id"]

    disabled = client.patch(f"/api/v1/trigger-bindings/{bid}", json={"enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False

    deleted = client.delete(f"/api/v1/trigger-bindings/{bid}")
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/trigger-bindings/{bid}").status_code == 404
