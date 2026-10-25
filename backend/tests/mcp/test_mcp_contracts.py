"""MCP data-contract reads (ADR 0052, ADR 0008 amendment 56 → 59)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastmcp.exceptions import ToolError

from backend.app.contracts import odcs
from backend.app.db.models import Connection, Result, Run, Share, Suite, User
from backend.app.mcp import server
from backend.app.services import contract_service


def _user(db: Any) -> User:
    u = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@acme.io")
    db.add(u)
    db.flush()
    return u


def _as(monkeypatch: Any, db: Any, user: User) -> None:
    monkeypatch.setattr(server, "get_session", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    monkeypatch.setattr(server, "resolve_current_user", lambda _session: user)


_DOC: dict[str, Any] = {
    "apiVersion": "v3.2.0",
    "kind": "DataContract",
    "id": "orders-contract",
    "name": "orders",
    "version": "1.0.0",
    "status": "active",
    "schema": [
        {
            "name": "orders",
            "physicalName": "PUBLIC.ORDERS",
            "properties": [{"name": "order_id", "logicalType": "integer", "required": True}],
            "quality": [{"id": "floor", "metric": "rowCount", "mustBeGreaterThan": 0}],
        },
        {"name": "refunds", "physicalName": "PUBLIC.REFUNDS", "properties": [{"name": "id"}]},
    ],
    "slaProperties": [{"property": "retention", "value": 7, "unit": "y"}],
}


def _imported(db: Any, owner: User) -> contract_service.ImportOutcome:
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "a"},
        secret_ref="kv-sf",
        created_by=owner.id,
    )
    db.add(conn)
    db.flush()
    return contract_service.import_contract(
        db, document=_DOC, document_yaml=None, connection_id=conn.id, actor_id=owner.id
    )


def test_list_contracts_names_suites_and_counts_what_is_hidden(
    db_session: Any, monkeypatch: Any
) -> None:
    owner = _user(db_session)
    outcome = _imported(db_session, owner)
    viewer = _user(db_session)
    db_session.add(
        Share(suite_id=outcome.created_suite_ids[0], user_id=viewer.id, permission="view")
    )
    db_session.commit()

    _as(monkeypatch, db_session, viewer)
    listed = server.list_contracts()
    assert (listed["total"], listed["returned"], listed["truncated"]) == (1, 1, False)
    entry = listed["contracts"][0]
    assert (entry["odcs_id"], entry["version"], entry["latest_version"]) == (
        "orders-contract",
        "1.0.0",
        True,
    )
    assert len(entry["suites"]) == 1 and entry["restricted_suite_count"] == 1
    assert [u["field"] for u in entry["unmapped"]] == ["slaProperties.retention"]

    _as(monkeypatch, db_session, _user(db_session))
    assert server.list_contracts()["contracts"] == []


def test_get_contract_reports_clause_states_and_finality(db_session: Any, monkeypatch: Any) -> None:
    owner = _user(db_session)
    outcome = _imported(db_session, owner)
    orders = db_session.get(Suite, outcome.created_suite_ids[0])
    floor = next(c for c in orders.checks if (c.contract_ref or {}).get("rule_id") == "floor")
    run = Run(suite_id=orders.id, status="running")
    db_session.add(run)
    db_session.flush()
    db_session.add(Result(run_id=run.id, check_id=floor.id, status="fail"))
    db_session.commit()

    _as(monkeypatch, db_session, owner)
    body = server.get_contract(str(outcome.contract.id))
    clause = next(c for c in body["compliance"]["clauses"] if c["clause"]["rule_id"] == "floor")
    assert clause["state"] == "fail" and clause["drifted"] is False
    suite_state = next(s for s in body["compliance"]["suites"] if s["suite_id"] == str(orders.id))
    assert suite_state["results_final"] is False  # still running: not a verdict
    assert body["compliance"]["not_enforced"] == 1
    assert [u["field"] for u in body["unmapped"]] == ["slaProperties.retention"]


def test_get_and_export_refuse_a_contract_you_cannot_see(db_session: Any, monkeypatch: Any) -> None:
    owner = _user(db_session)
    outcome = _imported(db_session, owner)
    _as(monkeypatch, db_session, _user(db_session))
    for tool in (server.get_contract, server.export_contract):
        with pytest.raises(ToolError, match="not found"):
            tool(str(outcome.contract.id))
    with pytest.raises(ToolError):
        server.get_contract("not-a-uuid")


def test_export_contract_returns_a_valid_odcs_document(db_session: Any, monkeypatch: Any) -> None:
    owner = _user(db_session)
    outcome = _imported(db_session, owner)
    _as(monkeypatch, db_session, owner)
    body = server.export_contract(str(outcome.contract.id))
    assert body["restricted_suite_count"] == 0
    document = body["document"]
    assert document["apiVersion"] == odcs.NEWEST_VERSION
    odcs.validate_document(document)


def test_list_contracts_pages(db_session: Any, monkeypatch: Any) -> None:
    owner = _user(db_session)
    _imported(db_session, owner)
    _as(monkeypatch, db_session, owner)
    page = server.list_contracts(limit=1, offset=0)
    assert page["returned"] == 1 and page["truncated"] is False


def test_get_check_and_list_suites_name_the_contract(db_session: Any, monkeypatch: Any) -> None:
    owner = _user(db_session)
    outcome = _imported(db_session, owner)
    orders = db_session.get(Suite, outcome.created_suite_ids[0])
    check = next(c for c in orders.checks if (c.contract_ref or {}).get("rule_id") == "floor")
    _as(monkeypatch, db_session, owner)
    got = server.get_check(str(orders.id), str(check.id))
    assert got["origin"] == "contract"
    assert got["contract_clause"]["odcs_id"] == "orders-contract"
    suites = server.list_suites()
    entry = next(x for x in suites if x["id"] == str(orders.id))
    assert entry["contract_id"] == str(outcome.contract.id)
