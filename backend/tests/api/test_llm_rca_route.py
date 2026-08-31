"""POST /llm/rca_narrative (ADR 0042, #1633): gates, dispatch, failure shapes."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.api.v1 import llm as llm_router_module
from backend.app.core.secrets import get_secret_store
from backend.app.db.models import Check, Connection, Incident, Result, Run, Share, Suite, User
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import incident_service, suite_service
from backend.tests.support.fake_secret_store import FakeSecretStore
from backend.tests.support.llm_helpers import enable_llm

_SF_CONFIG = {"account": "ab12345.eu-west-1", "database": "ANALYTICS", "schema": "PUBLIC"}


@pytest.fixture
def store() -> FakeSecretStore:
    return FakeSecretStore()


@pytest.fixture
def client(db_session: Any, store: FakeSecretStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_secret_store] = lambda: store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def dispatched(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(
        llm_router_module, "dispatch_llm_invocation", lambda inv_id: calls.append(str(inv_id))
    )
    return calls


def _connection(db: Any, owner: User) -> Connection:
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config=_SF_CONFIG,
        secret_ref="kv-x",
        created_by=owner.id,
    )
    db.add(conn)
    db.commit()
    return conn


def _suite(db: Any, owner: User, conn: Connection) -> Suite:
    return suite_service.create_suite(
        db,
        name=f"s-{uuid.uuid4().hex[:6]}",
        description=None,
        connection_id=conn.id,
        created_by=owner.id,
        target={"table": "ORDERS"},
    )


def _breach(db: Any, suite: Suite) -> Incident:
    check = Check(
        suite_id=suite.id,
        name="orders_not_null",
        kind="expectation",
        expectation_type="expect_column_values_to_not_be_null",
        config={"column": "id"},
    )
    db.add(check)
    db.flush()
    run = Run(suite_id=suite.id, status="succeeded", asset_id=suite.asset_id)
    db.add(run)
    db.flush()
    db.add(Result(run_id=run.id, check_id=check.id, status="fail", metric_value=0.4))
    db.commit()
    incident_service.sync_incidents_for_run(db, run_id=run.id)
    incident: Incident | None = db.scalars(
        select(Incident).where(Incident.suite_id == suite.id).order_by(Incident.created_at.desc())
    ).first()
    assert incident is not None
    return incident


def _body(incident: Incident) -> dict[str, Any]:
    return {"incident_id": str(incident.id)}


def test_rca_queues_and_dispatches(
    client: TestClient,
    db_session: Any,
    store: FakeSecretStore,
    dispatched: list[str],
    as_role: Callable[..., tuple[Any, dict[str, str]]],
) -> None:
    owner, headers = as_role("member")
    enable_llm(db_session, owner, store)
    conn = _connection(db_session, owner)
    suite = _suite(db_session, owner, conn)
    incident = _breach(db_session, suite)

    resp = client.post("/api/v1/llm/rca_narrative", json=_body(incident), headers=headers)
    assert resp.status_code == 202, resp.text
    invocation_id = resp.json()["invocation_id"]
    assert dispatched == [invocation_id]
    poll = client.get(f"/api/v1/llm/invocations/{invocation_id}", headers=headers)
    assert poll.status_code == 200
    assert poll.json()["status"] == "pending"
    assert poll.json()["kind"] == "rca_narrative"


def test_rca_only_requires_view_not_edit(
    client: TestClient,
    db_session: Any,
    store: FakeSecretStore,
    dispatched: list[str],
    as_role: Callable[..., tuple[Any, dict[str, str]]],
) -> None:
    """Unlike sql_generation/check_suggestions, nothing is ever saved to the
    suite — RCA is gated the same as reading the incident itself.
    """
    owner, _ = as_role("member")
    enable_llm(db_session, owner, store)
    conn = _connection(db_session, owner)
    suite = _suite(db_session, owner, conn)
    incident = _breach(db_session, suite)

    viewer, viewer_headers = as_role("member")
    db_session.add(Share(suite_id=suite.id, user_id=viewer.id, permission="view"))
    db_session.commit()

    resp = client.post("/api/v1/llm/rca_narrative", json=_body(incident), headers=viewer_headers)
    assert resp.status_code == 202, resp.text
    assert dispatched


def test_rca_hides_an_incident_on_an_invisible_suite(
    client: TestClient,
    db_session: Any,
    store: FakeSecretStore,
    dispatched: list[str],
    as_role: Callable[..., tuple[Any, dict[str, str]]],
) -> None:
    owner, _ = as_role("member")
    enable_llm(db_session, owner, store)
    conn = _connection(db_session, owner)
    suite = _suite(db_session, owner, conn)
    incident = _breach(db_session, suite)

    _, outsider_headers = as_role("member")
    resp = client.post("/api/v1/llm/rca_narrative", json=_body(incident), headers=outsider_headers)
    assert resp.status_code == 404
    assert dispatched == []


def test_rca_unknown_incident_is_404(
    client: TestClient,
    db_session: Any,
    store: FakeSecretStore,
    dispatched: list[str],
    as_role: Callable[..., tuple[Any, dict[str, str]]],
) -> None:
    owner, headers = as_role("member")
    enable_llm(db_session, owner, store)
    resp = client.post(
        "/api/v1/llm/rca_narrative", json={"incident_id": str(uuid.uuid4())}, headers=headers
    )
    assert resp.status_code == 404
    assert dispatched == []


def test_rca_refuses_an_incident_with_no_evidence_card(
    client: TestClient,
    db_session: Any,
    store: FakeSecretStore,
    dispatched: list[str],
    as_role: Callable[..., tuple[Any, dict[str, str]]],
) -> None:
    owner, headers = as_role("member")
    enable_llm(db_session, owner, store)
    conn = _connection(db_session, owner)
    suite = _suite(db_session, owner, conn)
    incident = _breach(db_session, suite)
    incident.evidence = None
    db_session.add(incident)
    db_session.commit()

    resp = client.post("/api/v1/llm/rca_narrative", json=_body(incident), headers=headers)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "llm_request_invalid"
    assert dispatched == []


def test_rca_unconfigured_is_409_not_500(
    client: TestClient,
    db_session: Any,
    dispatched: list[str],
    as_role: Callable[..., tuple[Any, dict[str, str]]],
) -> None:
    owner, headers = as_role("member")
    conn = _connection(db_session, owner)
    suite = _suite(db_session, owner, conn)
    incident = _breach(db_session, suite)

    resp = client.post("/api/v1/llm/rca_narrative", json=_body(incident), headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "llm_not_configured"
    assert dispatched == []


def test_rca_garbage_incident_id_is_422(
    client: TestClient,
    db_session: Any,
    store: FakeSecretStore,
    dispatched: list[str],
    as_role: Callable[..., tuple[Any, dict[str, str]]],
) -> None:
    owner, headers = as_role("member")
    enable_llm(db_session, owner, store)
    resp = client.post(
        "/api/v1/llm/rca_narrative", json={"incident_id": "not-a-uuid"}, headers=headers
    )
    assert resp.status_code == 422
    assert dispatched == []
