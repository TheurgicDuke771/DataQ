"""A result's `expected_value` echoes the check's kwargs; on a sensitive column its values are
withheld on every door, as the check read withholds them (#2686)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.core.auth import get_current_user
from backend.app.db.models import Asset, Check, Connection, Incident, Result, Run, User
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.mcp import server
from backend.app.services import incident_service, llm_rca, suite_service
from backend.app.services.incident_evidence import build_evidence

SECRETS = ("tier-gold-77", "tier-silver-77")
_M = "<redacted>"


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def leaked(db_session: Any) -> dict[str, Any]:
    """An in-set check on `TIER`, which the suite's policy marks sensitive, and one failing
    result whose expected_value carries the set (as GX writes it)."""
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:6]}@ex.io")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "ab12345.eu-west-1", "database": "HR", "schema": "PUBLIC"},
        secret_ref="kv-x",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.commit()
    suite = suite_service.create_suite(
        db_session,
        name=f"s-{uuid.uuid4().hex[:6]}",
        description=None,
        connection_id=conn.id,
        created_by=owner.id,
        target={"table": "MEMBERS"},
    )
    suite.column_policy = {"pii_columns": ["TIER"]}
    check = Check(
        suite_id=suite.id,
        name="tier in set",
        kind="expectation",
        expectation_type="expect_column_values_to_be_in_set",
        config={"column": "TIER", "value_set": list(SECRETS)},
    )
    db_session.add(check)
    db_session.flush()
    run = Run(
        suite_id=suite.id,
        status="succeeded",
        triggered_by="manual",
        asset_id=suite.asset_id,
        finished_at=datetime.now(UTC),
    )
    db_session.add(run)
    db_session.flush()
    result = Result(
        run_id=run.id,
        check_id=check.id,
        status="fail",
        observed_value={"unexpected_count": 3},
        expected_value={"column": "TIER", "value_set": list(SECRETS), "mostly": 1.0},
    )
    db_session.add(result)
    db_session.commit()
    return {"owner": owner, "suite": suite, "check": check, "run": run, "result": result}


def _clean(payload: Any) -> bool:
    text = json.dumps(payload, default=str)
    return not any(secret in text for secret in SECRETS)


def test_the_run_results_api_withholds_the_set(client: TestClient, leaked: dict[str, Any]) -> None:
    app.dependency_overrides[get_current_user] = lambda: leaked["owner"]
    body = client.get(f"/api/v1/runs/{leaked['run'].id}").json()
    (row,) = body["results"]
    assert row["expected_value"]["value_set"] == [_M, _M]
    assert row["expected_value"]["mostly"] == 1.0  # options are kept
    assert _clean(body)


def test_the_mcp_run_results_tool_withholds_the_set(
    db_session: Any, monkeypatch: Any, leaked: dict[str, Any]
) -> None:
    monkeypatch.setattr(server, "get_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(server, "resolve_current_user", lambda _session: leaked["owner"])
    out = server.get_run_results(str(leaked["run"].id))
    assert _clean(out)


def test_a_new_evidence_card_is_written_withheld(db_session: Any, leaked: dict[str, Any]) -> None:
    run = leaked["run"]
    card = build_evidence(
        db_session,
        run=run,
        result=leaked["result"],
        check=leaked["check"],
        asset=db_session.get(Asset, run.asset_id),
    )
    assert card["failing_result"]["expected_value"]["value_set"] == [_M, _M]
    assert _clean(card)


def test_a_card_stored_before_the_fix_is_withheld_when_read_and_in_the_rca_prompt(
    db_session: Any, leaked: dict[str, Any]
) -> None:
    result = leaked["result"]
    incident = Incident(
        asset_id=leaked["suite"].asset_id,
        check_id=leaked["check"].id,
        suite_id=leaked["suite"].id,
        status="open",
        occurrence_count=1,
        last_seen_at=result.created_at,
        evidence={"failing_result": {"status": "fail", "expected_value": result.expected_value}},
    )
    db_session.add(incident)
    db_session.commit()

    evidence = incident_service.evidence_for_caller(
        db_session, incident, user_id=leaked["owner"].id
    )
    assert evidence is not None and _clean(evidence)
    assert _clean(llm_rca._render_evidence(evidence, [], []))


def test_an_unclassified_column_keeps_its_set(client: TestClient, leaked: dict[str, Any]) -> None:
    leaked["suite"].column_policy = None
    app.dependency_overrides[get_current_user] = lambda: leaked["owner"]
    row = client.get(f"/api/v1/runs/{leaked['run'].id}").json()["results"][0]
    assert row["expected_value"]["value_set"] == list(SECRETS)
