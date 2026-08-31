"""POST /llm/sql_generation (ADR 0042, #1512): gates, dispatch, failure shapes."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.api.v1 import llm as llm_router_module
from backend.app.core.secrets import get_secret_store
from backend.app.db.models import LlmInvocation, Suite
from backend.app.db.session import get_db
from backend.app.main import app
from backend.tests.support.fake_secret_store import FakeSecretStore
from backend.tests.support.llm_helpers import enable_llm, make_sql_suite


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
    enable_llm(db_session, owner, store)
    suite = make_sql_suite(db_session, owner)
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
    enable_llm(db_session, owner, store)
    suite = make_sql_suite(db_session, owner)
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
    enable_llm(db_session, owner, store)
    suite = make_sql_suite(db_session, owner, conn_type="s3", target={"path": "x.csv"})
    resp = client.post("/api/v1/llm/sql_generation", json=_body(suite), headers=headers)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "llm_request_invalid"
    assert dispatched == []


def test_generation_unconfigured_is_409_not_500(
    client: TestClient,
    db_session: Any,
    dispatched: list[str],
    as_role: Callable[..., tuple[Any, dict[str, str]]],
) -> None:
    owner, headers = as_role("member")
    suite = make_sql_suite(db_session, owner)
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
    enable_llm(db_session, owner, store)
    suite = make_sql_suite(db_session, owner)
    resp = client.post(
        "/api/v1/llm/sql_generation", json=_body(suite, description="   "), headers=headers
    )
    assert resp.status_code == 422


def test_generation_targetless_suite_is_a_synchronous_422(
    client: TestClient,
    db_session: Any,
    store: FakeSecretStore,
    dispatched: list[str],
    as_role: Callable[..., tuple[Any, dict[str, str]]],
) -> None:
    """The route and the builder share one precondition set — no 202-then-fail."""
    owner, headers = as_role("member")
    enable_llm(db_session, owner, store)
    suite = make_sql_suite(db_session, owner, target={})
    resp = client.post("/api/v1/llm/sql_generation", json=_body(suite), headers=headers)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "llm_request_invalid"
    assert dispatched == []


def test_generation_overlong_description_is_refused_not_truncated(
    client: TestClient,
    db_session: Any,
    store: FakeSecretStore,
    dispatched: list[str],
    as_role: Callable[..., tuple[Any, dict[str, str]]],
) -> None:
    owner, headers = as_role("member")
    enable_llm(db_session, owner, store)
    suite = make_sql_suite(db_session, owner)
    resp = client.post(
        "/api/v1/llm/sql_generation",
        json=_body(suite, description="x" * 2001),
        headers=headers,
    )
    assert resp.status_code == 422
    assert dispatched == []


def test_broker_failure_lands_the_row_failed_and_503s(
    client: TestClient,
    db_session: Any,
    store: FakeSecretStore,
    monkeypatch: pytest.MonkeyPatch,
    as_role: Callable[..., tuple[Any, dict[str, str]]],
) -> None:
    owner, headers = as_role("member")
    enable_llm(db_session, owner, store)
    suite = make_sql_suite(db_session, owner)

    def _boom(_inv_id: Any) -> None:
        raise RuntimeError("broker down")

    monkeypatch.setattr(llm_router_module, "dispatch_llm_invocation", _boom)
    resp = client.post("/api/v1/llm/sql_generation", json=_body(suite), headers=headers)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "llm_dispatch_failed"
    rows = db_session.query(LlmInvocation).filter(LlmInvocation.suite_id == suite.id).all()
    assert len(rows) == 1
    assert rows[0].status == "failed"
    assert rows[0].finished_at is not None


def test_generation_stores_additional_tables_on_the_invocation(
    client: TestClient,
    db_session: Any,
    store: FakeSecretStore,
    dispatched: list[str],
    as_role: Callable[..., tuple[Any, dict[str, str]]],
) -> None:
    owner, headers = as_role("member")
    enable_llm(db_session, owner, store)
    suite = make_sql_suite(db_session, owner)
    resp = client.post(
        "/api/v1/llm/sql_generation",
        json=_body(suite, additional_tables=[{"table": "TRAFFIC", "schema": "ANALYTICS"}]),
        headers=headers,
    )
    assert resp.status_code == 202, resp.text
    row = db_session.query(LlmInvocation).filter(LlmInvocation.suite_id == suite.id).one()
    assert row.request is not None
    assert row.request["additional_tables"] == [
        {"table": "TRAFFIC", "schema": "ANALYTICS", "catalog": None}
    ]


def test_generation_additional_tables_over_the_cap_is_a_synchronous_422(
    client: TestClient,
    db_session: Any,
    store: FakeSecretStore,
    dispatched: list[str],
    as_role: Callable[..., tuple[Any, dict[str, str]]],
) -> None:
    owner, headers = as_role("member")
    enable_llm(db_session, owner, store)
    suite = make_sql_suite(db_session, owner)
    from backend.app.services import llm_sqlgen

    too_many = [{"table": f"T{i}"} for i in range(llm_sqlgen.MAX_ADDITIONAL_TABLES + 1)]
    resp = client.post(
        "/api/v1/llm/sql_generation",
        json=_body(suite, additional_tables=too_many),
        headers=headers,
    )
    assert resp.status_code == 422
    assert dispatched == []


def test_generation_additional_table_blank_name_is_a_synchronous_422(
    client: TestClient,
    db_session: Any,
    store: FakeSecretStore,
    dispatched: list[str],
    as_role: Callable[..., tuple[Any, dict[str, str]]],
) -> None:
    owner, headers = as_role("member")
    enable_llm(db_session, owner, store)
    suite = make_sql_suite(db_session, owner)
    resp = client.post(
        "/api/v1/llm/sql_generation",
        json=_body(suite, additional_tables=[{"table": "   "}]),
        headers=headers,
    )
    assert resp.status_code == 422
    assert dispatched == []


def test_generation_duplicate_additional_table_is_a_synchronous_422(
    client: TestClient,
    db_session: Any,
    store: FakeSecretStore,
    dispatched: list[str],
    as_role: Callable[..., tuple[Any, dict[str, str]]],
) -> None:
    """The dedup check (#1649 review) is shared by the route and the worker's
    TOCTOU re-check, same as every other precondition in this file — a bad
    additional_tables list must not queue and only fail later.
    """
    owner, headers = as_role("member")
    enable_llm(db_session, owner, store)
    suite = make_sql_suite(db_session, owner)
    resp = client.post(
        "/api/v1/llm/sql_generation",
        json=_body(suite, additional_tables=[{"table": "TRAFFIC"}, {"table": "traffic"}]),
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "llm_request_invalid"
    assert dispatched == []


def test_additional_table_ref_has_no_connection_field() -> None:
    """Cross-connection joins are refused structurally, not at runtime: the
    request shape itself carries no way to name a different connection.
    """
    from backend.app.api.v1.llm import AdditionalTableRef

    fields = AdditionalTableRef.model_fields
    assert "connection_id" not in fields
    assert "connection" not in fields
    assert set(fields) == {"table", "schema_", "catalog"}


def test_dispatch_failure_never_clobbers_a_claimed_row(
    client: TestClient,
    db_session: Any,
    store: FakeSecretStore,
    monkeypatch: pytest.MonkeyPatch,
    as_role: Callable[..., tuple[Any, dict[str, str]]],
) -> None:
    """send_task can raise AFTER the message was effectively published; if the
    worker already claimed the row, the route's failure write must not win.
    """
    owner, headers = as_role("member")
    enable_llm(db_session, owner, store)
    suite = make_sql_suite(db_session, owner)

    def _publish_then_raise(inv_id: Any) -> None:
        from sqlalchemy import update

        db_session.execute(
            update(LlmInvocation).where(LlmInvocation.id == inv_id).values(status="running")
        )
        db_session.commit()
        raise RuntimeError("broker error after effective publish")

    monkeypatch.setattr(llm_router_module, "dispatch_llm_invocation", _publish_then_raise)
    resp = client.post("/api/v1/llm/sql_generation", json=_body(suite), headers=headers)
    assert resp.status_code == 503
    row = db_session.query(LlmInvocation).filter(LlmInvocation.suite_id == suite.id).one()
    db_session.refresh(row)
    assert row.status == "running"  # the claim survived; no clobber to failed
