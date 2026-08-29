"""POST /llm/sql_generation (ADR 0042, #1512): gates, dispatch, failure shapes."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.api.v1 import llm as llm_router_module
from backend.app.core.secrets import get_secret_store
from backend.app.db.models import Connection, Suite, User
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import llm_service
from backend.tests.support.fake_secret_store import FakeSecretStore


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


def _configure_llm(db_session: Any, store: FakeSecretStore, actor: User) -> None:
    llm_service.save_settings(
        db_session,
        draft=llm_service.LlmSettingsDraft(
            provider="openai_compatible", model="m", base_url="http://x/v1", api_key="k"
        ),
        actor=actor,
        secret_store=store,
    )
    db_session.commit()


def _suite(db_session: Any, owner: User, conn_type: str = "snowflake") -> Suite:
    connection = Connection(
        id=uuid.uuid4(),
        name=f"c-{uuid.uuid4().hex[:6]}",
        type=conn_type,
        env="dev",
        config={},
        created_by=owner.id,
    )
    db_session.add(connection)
    db_session.flush()
    suite = Suite(
        name=f"s-{uuid.uuid4().hex[:6]}",
        connection_id=connection.id,
        created_by=owner.id,
        target={"table": "ORDERS", "schema": "RETAIL"},
    )
    db_session.add(suite)
    db_session.commit()
    return suite


def _body(suite: Suite, **extra: Any) -> dict[str, Any]:
    return {"suite_id": str(suite.id), "description": "no null emails", **extra}


def test_generation_queues_and_dispatches(
    client: TestClient,
    db_session: Any,
    store: FakeSecretStore,
    dispatched: list[str],
    as_role: Callable[..., tuple[Any, dict[str, str]]],
) -> None:
    owner, headers = as_role("member")
    _configure_llm(db_session, store, owner)
    suite = _suite(db_session, owner)
    resp = client.post("/api/v1/llm/sql_generation", json=_body(suite), headers=headers)
    assert resp.status_code == 202, resp.text
    invocation_id = resp.json()["invocation_id"]
    assert dispatched == [invocation_id]
    poll = client.get(f"/api/v1/llm/invocations/{invocation_id}", headers=headers)
    assert poll.status_code == 200
    assert poll.json()["status"] == "pending"


def test_generation_requires_edit_grant(
    client: TestClient,
    db_session: Any,
    store: FakeSecretStore,
    dispatched: list[str],
    as_role: Callable[..., tuple[Any, dict[str, str]]],
) -> None:
    owner, _ = as_role("member")
    _configure_llm(db_session, store, owner)
    suite = _suite(db_session, owner)
    _, stranger_headers = as_role("member")
    resp = client.post("/api/v1/llm/sql_generation", json=_body(suite), headers=stranger_headers)
    assert resp.status_code in (403, 404)
    assert dispatched == []


def test_generation_refuses_non_sql_datasource(
    client: TestClient,
    db_session: Any,
    store: FakeSecretStore,
    dispatched: list[str],
    as_role: Callable[..., tuple[Any, dict[str, str]]],
) -> None:
    owner, headers = as_role("member")
    _configure_llm(db_session, store, owner)
    suite = _suite(db_session, owner, conn_type="s3")
    resp = client.post("/api/v1/llm/sql_generation", json=_body(suite), headers=headers)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "llm_target_not_sql"
    assert dispatched == []


def test_generation_unconfigured_is_409_not_500(
    client: TestClient,
    db_session: Any,
    dispatched: list[str],
    as_role: Callable[..., tuple[Any, dict[str, str]]],
) -> None:
    owner, headers = as_role("member")
    suite = _suite(db_session, owner)
    resp = client.post("/api/v1/llm/sql_generation", json=_body(suite), headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "llm_not_configured"
    assert dispatched == []


def test_generation_blank_description_is_422(
    client: TestClient,
    db_session: Any,
    store: FakeSecretStore,
    as_role: Callable[..., tuple[Any, dict[str, str]]],
) -> None:
    owner, headers = as_role("member")
    _configure_llm(db_session, store, owner)
    suite = _suite(db_session, owner)
    resp = client.post(
        "/api/v1/llm/sql_generation", json=_body(suite, description="   "), headers=headers
    )
    assert resp.status_code == 422


def test_broker_failure_lands_the_row_failed_and_503s(
    client: TestClient,
    db_session: Any,
    store: FakeSecretStore,
    monkeypatch: pytest.MonkeyPatch,
    as_role: Callable[..., tuple[Any, dict[str, str]]],
) -> None:
    owner, headers = as_role("member")
    _configure_llm(db_session, store, owner)
    suite = _suite(db_session, owner)

    def _boom(_inv_id: Any) -> None:
        raise RuntimeError("broker down")

    monkeypatch.setattr(llm_router_module, "dispatch_llm_invocation", _boom)
    resp = client.post("/api/v1/llm/sql_generation", json=_body(suite), headers=headers)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "llm_dispatch_failed"
    from backend.app.db.models import LlmInvocation

    rows = db_session.query(LlmInvocation).filter(LlmInvocation.suite_id == suite.id).all()
    assert len(rows) == 1
    assert rows[0].status == "failed"
