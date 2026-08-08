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
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"owner-{uuid.uuid4().hex[:8]}@example.com")
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
    other = User(aad_object_id=uuid.uuid4().hex, email=f"other-{uuid.uuid4().hex[:8]}@example.com")
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


def _adf_connection(db_session: Any, *, env: str, factory: str) -> Connection:
    """An orchestration (ADF) connection — distinct from `_connection`'s
    Snowflake stand-in, used for the #1186 ambiguous-URL warning tests."""
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"adf-{uuid.uuid4().hex[:8]}@example.com")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"adf-{env}-{uuid.uuid4().hex[:8]}",
        type="adf",
        env=env,
        config={"factory_name": factory},
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.commit()
    return conn


def test_create_then_get_binding(client: TestClient, db_session: Any) -> None:
    suite_id = _owned_suite(client, _connection(db_session).id)
    created = client.post("/api/v1/trigger-bindings", json=_payload(suite_id))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["provider"] == "adf"
    assert body["enabled"] is True
    # No orchestration connection at all for (adf, dev) here — nothing to compare,
    # so the #1186 warning is silent (and the response shape stays additive).
    assert body["warnings"] == []

    got = client.get(f"/api/v1/trigger-bindings/{body['id']}")
    assert got.status_code == 200
    assert got.json()["suite_id"] == suite_id
    assert got.json()["warnings"] == []  # a plain GET never recomputes warnings


# ── #1186: creation/update-time ambiguous-orchestration-URL warning ──────────


def test_create_warns_on_cross_env_shared_url(client: TestClient, db_session: Any) -> None:
    # Two ADF connections share one factory_name across envs — the live #1186
    # shape. The binding is created against "dev"; the response must warn that
    # "qa" shares the same resource.
    _adf_connection(db_session, env="dev", factory="shared-factory")
    _adf_connection(db_session, env="qa", factory="shared-factory")
    # The suite's datasource is unrelated to the ADF connections above — a
    # binding never references a connection_id (CLAUDE.md §4: orchestration
    # providers cannot be a suite's datasource), only (provider, dag, env).
    suite_id = _owned_suite(client, _connection(db_session).id)

    resp = client.post("/api/v1/trigger-bindings", json=_payload(suite_id, env="dev"))
    assert resp.status_code == 201, resp.text
    warnings = resp.json()["warnings"]
    assert len(warnings) == 1
    assert warnings[0]["code"] == "ambiguous_orchestration_url"
    assert warnings[0]["other_envs"] == ["qa"]


def test_create_does_not_warn_without_a_shared_url(client: TestClient, db_session: Any) -> None:
    _adf_connection(db_session, env="dev", factory="factory-dev-only")
    _adf_connection(db_session, env="qa", factory="factory-qa-only")  # distinct — no ambiguity
    suite_id = _owned_suite(client, _connection(db_session).id)

    resp = client.post("/api/v1/trigger-bindings", json=_payload(suite_id, env="dev"))
    assert resp.status_code == 201, resp.text
    assert resp.json()["warnings"] == []


def test_disabled_binding_creation_does_not_warn(client: TestClient, db_session: Any) -> None:
    # The ambiguity is real, but a disabled binding won't fire regardless — the
    # warning isn't actionable yet, so it's suppressed until the binding is
    # (re-)enabled.
    _adf_connection(db_session, env="dev", factory="shared-factory-2")
    _adf_connection(db_session, env="qa", factory="shared-factory-2")
    suite_id = _owned_suite(client, _connection(db_session).id)

    resp = client.post(
        "/api/v1/trigger-bindings", json=_payload(suite_id, env="dev", enabled=False)
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["warnings"] == []


def test_reenabling_a_binding_recomputes_the_warning(client: TestClient, db_session: Any) -> None:
    _adf_connection(db_session, env="dev", factory="shared-factory-3")
    _adf_connection(db_session, env="qa", factory="shared-factory-3")
    suite_id = _owned_suite(client, _connection(db_session).id)

    created = client.post(
        "/api/v1/trigger-bindings", json=_payload(suite_id, env="dev", enabled=False)
    )
    assert created.json()["warnings"] == []
    bid = created.json()["id"]

    reenabled = client.patch(f"/api/v1/trigger-bindings/{bid}", json={"enabled": True})
    assert reenabled.status_code == 200
    warnings = reenabled.json()["warnings"]
    assert len(warnings) == 1
    assert warnings[0]["code"] == "ambiguous_orchestration_url"
    assert warnings[0]["other_envs"] == ["qa"]


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
    resp = client.get(f"/api/v1/trigger-bindings/{bid}")
    assert resp.status_code == 404
