"""Trigger-binding endpoint tests (TestClient + real Postgres)."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

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
    Snowflake stand-in, used for the #1186 ambiguous-URL warning tests.
    """
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
    # Two ADF connections share one factory_name across envs — the live #1186 shape.
    _adf_connection(db_session, env="dev", factory="shared-factory")
    _adf_connection(db_session, env="qa", factory="shared-factory")
    # The suite's datasource is unrelated to the ADF connections above — a binding never references
    # a connection_id (CLAUDE.md §4: orchestration providers cannot be a suite's datasource).
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
    # The ambiguity is real, but a disabled binding won't fire regardless — the warning isn't
    # actionable yet, so it's suppressed until the binding is (re-)enabled.
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


def test_create_on_inaccessible_suite_is_404(
    client: TestClient, db_session: Any, as_role: Any
) -> None:
    # A suite the caller has no access to is hidden (404), not 403 — and no row.
    _, headers = as_role("member")
    conn = _connection(db_session)
    suite = _unowned_suite(db_session, conn)
    resp = client.post("/api/v1/trigger-bindings", json=_payload(str(suite.id)), headers=headers)
    assert resp.status_code == 404
    assert db_session.scalar(select(func.count()).select_from(TriggerBinding)) == 0


def test_create_with_view_only_is_forbidden(
    client: TestClient, db_session: Any, as_role: Any
) -> None:
    from backend.app.db.models import Share

    # A genuine MEMBER principal: the ambient dev-bypass identity is a workspace admin since #741,
    # and a workspace admin is implicit `admin` on EVERY suite (ADR 0027).
    member, headers = as_role("member")
    conn = _connection(db_session)
    suite = _unowned_suite(db_session, conn)
    db_session.add(Share(suite_id=suite.id, user_id=member.id, permission="view"))
    db_session.commit()

    # creating a binding needs `edit`; view-only → 403 (access exists, too low)
    resp = client.post("/api/v1/trigger-bindings", json=_payload(str(suite.id)), headers=headers)
    assert resp.status_code == 403


def test_create_on_missing_suite_404(client: TestClient) -> None:
    resp = client.post("/api/v1/trigger-bindings", json=_payload(str(uuid.uuid4())))
    assert resp.status_code == 404


def test_list_is_scoped_to_accessible_suites(
    client: TestClient, db_session: Any, as_role: Any
) -> None:
    """Authenticated as a real **member**, not the ambient dev-bypass identity."""
    actor, headers = as_role("member")
    conn = _connection(db_session)
    mine = Suite(
        id=uuid.uuid4(),
        name=f"s-{uuid.uuid4().hex[:8]}",
        connection_id=conn.id,
        created_by=actor.id,
    )
    db_session.add(mine)
    db_session.commit()
    client.post("/api/v1/trigger-bindings", json=_payload(str(mine.id)), headers=headers)
    # a binding on a suite I don't own (inserted directly) must not show
    theirs = _unowned_suite(db_session, conn)
    db_session.add(
        TriggerBinding(provider="adf", pipeline_or_dag_id="other", env="dev", suite_id=theirs.id)
    )
    db_session.commit()

    listed = client.get("/api/v1/trigger-bindings", headers=headers)
    assert listed.status_code == 200
    suite_ids = {b["suite_id"] for b in listed.json()}
    assert suite_ids == {str(mine.id)}


def test_list_gives_a_workspace_admin_every_binding(
    client: TestClient, db_session: Any, as_role: Any
) -> None:
    """Parity with `/schedules`, which has honoured the workspace-admin view since
    #488 — an admin seeing every schedule but only their own bindings is a
    difference with no rationale behind it (ADR 0027).
    """
    _, headers = as_role("admin")
    conn = _connection(db_session)
    theirs = _unowned_suite(db_session, conn)
    db_session.add(
        TriggerBinding(provider="adf", pipeline_or_dag_id="other", env="dev", suite_id=theirs.id)
    )
    db_session.commit()

    listed = client.get("/api/v1/trigger-bindings", headers=headers)
    assert str(theirs.id) in {b["suite_id"] for b in listed.json()}


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


# ───────────────────────── suite-on-suite (ADR 0051, #244) ─────────────────────────


def _suite_on(db_session: Any, client: TestClient, *, env: str) -> str:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"o-{uuid.uuid4().hex[:8]}@example.com")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"pg-{uuid.uuid4().hex[:6]}", type="postgres", env=env, config={}, created_by=owner.id
    )
    db_session.add(conn)
    db_session.commit()
    return _owned_suite(client, conn.id)


def _suite_payload(upstream: str, downstream: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "provider": "suite",
        "pipeline_or_dag_id": upstream,
        "suite_id": downstream,
    }
    body.update(overrides)
    return body


def test_suite_binding_takes_the_upstream_env_whatever_was_sent(
    client: TestClient, db_session: Any
) -> None:
    upstream = _suite_on(db_session, client, env="qa")
    downstream = _suite_on(db_session, client, env="dev")
    resp = client.post(
        "/api/v1/trigger-bindings", json=_suite_payload(upstream, downstream, env="dev")
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert (body["provider"], body["pipeline_or_dag_id"], body["env"]) == ("suite", upstream, "qa")
    assert body["warnings"] == []  # the orchestration URL check does not apply
    # `env` may be omitted entirely.
    other = _suite_on(db_session, client, env="dev")
    resp_1 = client.post("/api/v1/trigger-bindings", json=_suite_payload(upstream, other))
    assert resp_1.status_code == 201


def test_suite_binding_refuses_self_and_a_non_uuid_upstream(
    client: TestClient, db_session: Any
) -> None:
    suite = _suite_on(db_session, client, env="dev")
    me = client.post("/api/v1/trigger-bindings", json=_suite_payload(suite, suite))
    assert me.status_code == 422 and "itself" in me.json()["error"]["message"]
    bad = client.post("/api/v1/trigger-bindings", json=_suite_payload("load_finance", suite))
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "trigger_binding_invalid"


def test_suite_binding_refuses_a_loop_even_through_a_disabled_hop(
    client: TestClient, db_session: Any
) -> None:
    a = _suite_on(db_session, client, env="dev")
    b = _suite_on(db_session, client, env="dev")
    c = _suite_on(db_session, client, env="dev")
    a_to_b = client.post("/api/v1/trigger-bindings", json=_suite_payload(a, b))
    assert a_to_b.status_code == 201
    b_to_c = client.post("/api/v1/trigger-bindings", json=_suite_payload(b, c, enabled=False))
    assert b_to_c.status_code == 201
    loop = client.post("/api/v1/trigger-bindings", json=_suite_payload(c, a))
    assert loop.status_code == 422, loop.text
    assert loop.json()["error"]["message"].startswith("this binding would make the suites trigger")
    assert loop.json()["error"]["detail"]["cycle"] == [a, b, c, a]
    assert db_session.scalar(select(func.count()).select_from(TriggerBinding)) == 2


def test_suite_binding_needs_view_on_the_upstream(
    client: TestClient, db_session: Any, as_role: Any
) -> None:
    _member, headers = as_role("member")
    conn = _connection(db_session)
    hidden = _unowned_suite(db_session, conn)
    mine = client.post(
        "/api/v1/suites",
        headers=headers,
        json={"name": f"s-{uuid.uuid4().hex[:8]}", "connection_id": str(conn.id)},
    )
    assert mine.status_code == 201, mine.text
    resp = client.post(
        "/api/v1/trigger-bindings",
        headers=headers,
        json=_suite_payload(str(hidden.id), mine.json()["id"]),
    )
    assert resp.status_code == 404  # hidden, not 403: no enumeration of suite ids
    assert db_session.scalar(select(func.count()).select_from(TriggerBinding)) == 0


def test_suite_binding_stores_the_canonical_upstream_id(
    client: TestClient, db_session: Any
) -> None:
    # Any spelling uuid.UUID() accepts must land as str(uuid): firing, the cycle walk and the UI
    # all compare against that form, so an upper-case input must not create a row that never fires.
    upstream = _suite_on(db_session, client, env="dev")
    downstream = _suite_on(db_session, client, env="dev")
    spelled = "{" + upstream.upper() + "}"
    resp = client.post("/api/v1/trigger-bindings", json=_suite_payload(spelled, downstream))
    assert resp.status_code == 201, resp.text
    assert resp.json()["pipeline_or_dag_id"] == upstream


def test_suite_binding_create_serialises_on_an_advisory_lock(
    client: TestClient, db_session: Any
) -> None:
    # Two concurrent creates (A→B and B→A) would both pass the cycle walk; the lock makes the
    # second wait for the first's commit and then see its row. The test session's outer
    # transaction keeps the xact lock visible after the service "commits" (a savepoint here).
    from backend.app.services.trigger_binding_service import SUITE_BINDING_LOCK_KEY

    upstream = _suite_on(db_session, client, env="dev")
    downstream = _suite_on(db_session, client, env="dev")
    created = client.post("/api/v1/trigger-bindings", json=_suite_payload(upstream, downstream))
    assert created.status_code == 201
    held = db_session.execute(
        text(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted "
            "AND ((classid::bigint << 32) | objid::bigint) = :key"
        ),
        {"key": SUITE_BINDING_LOCK_KEY},
    ).scalar_one()
    assert held == 1
