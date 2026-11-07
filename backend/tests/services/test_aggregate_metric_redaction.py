"""#2240: an `aggregate` min/max records one literal cell of its column as `metric_value`, so on
a sensitive column every read surface must withhold it — one test per surface.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.alerting import builder, webhook
from backend.app.core.auth import get_current_user
from backend.app.db.models import (
    Asset,
    Check,
    CheckVersion,
    Connection,
    Incident,
    Result,
    Run,
    User,
)
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.mcp import server
from backend.app.services import (
    check_service,
    dryrun_service,
    incident_service,
    llm_rca,
    run_service,
    suite_service,
)
from backend.app.services.incident_evidence import build_evidence

#: The literal cell. Distinctive enough that its absence from a serialized payload is meaningful.
SECRET = 987654.32
SECRET_TEXT = "987654.32"


# ── the shared helper ──────────────────────────────────────────────────────────


def _reason(
    aggregate: str | None,
    *,
    column: str | None = "SALARY",
    policy: dict[str, Any] | None = None,
    tags: dict[str, str] | None = None,
    expectation_type: str | None = "monitor:aggregate",
    observed: dict[str, Any] | None = None,
) -> str | None:
    return run_service.metric_value_redaction(
        SECRET,
        tested_column=column,
        expectation_type=expectation_type,
        aggregate=aggregate,
        observed=observed,
        policy=policy,
        tags=tags,
    )


@pytest.mark.parametrize("aggregate", ["min", "max"])
def test_a_min_or_max_on_a_policy_column_is_withheld(aggregate: str) -> None:
    assert _reason(aggregate, policy={"pii_columns": ["salary"]}) == "policy"


def test_a_min_on_a_warehouse_tagged_column_is_withheld() -> None:
    assert _reason("min", tags={"SALARY": "sensitive"}) == "own_tag"


@pytest.mark.parametrize("aggregate", ["mean", "median", "sum", "stdev"])
def test_a_computed_aggregate_on_a_policy_column_is_shown(aggregate: str) -> None:
    assert _reason(aggregate, policy={"pii_columns": ["SALARY"]}) is None


def test_a_min_on_an_unclassified_column_is_shown() -> None:
    assert _reason("min") is None


def test_a_gx_min_expectation_metric_is_not_a_cell() -> None:
    """GX's own min/max expectations keep their unexpected percent in `metric_value`."""
    assert (
        _reason(
            None,
            expectation_type="expect_column_min_to_be_between",
            policy={"pii_columns": ["SALARY"]},
        )
        is None
    )


def test_an_aggregate_nothing_names_fails_closed() -> None:
    assert _reason(None, policy={"pii_columns": ["SALARY"]}) == "policy"


def test_any_source_naming_min_or_max_is_enough() -> None:
    """The check as of the result says mean; the result itself measured a max."""
    observed = {"aggregate": "max", "observed_value": SECRET}
    assert _reason("mean", policy={"pii_columns": ["SALARY"]}, observed=observed) == "policy"


def test_a_cell_with_no_tested_column_fails_closed() -> None:
    assert _reason("max", column=None) == "default_mask"


def test_an_unknown_check_whose_result_names_a_max_is_withheld() -> None:
    observed = {"aggregate": "max", "observed_value": SECRET}
    assert (
        _reason(None, expectation_type=None, observed=observed, policy={"pii_columns": ["SALARY"]})
        == "policy"
    )


# ── world ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _owner(db: Any) -> User:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:6]}@ex.io")
    db.add(user)
    db.flush()
    return user


def _suite(db: Any, owner: User, *, policy: dict[str, Any] | None) -> Any:
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "ab12345.eu-west-1", "database": "HR", "schema": "PUBLIC"},
        secret_ref="kv-x",
        created_by=owner.id,
    )
    db.add(conn)
    db.commit()
    suite = suite_service.create_suite(
        db,
        name=f"s-{uuid.uuid4().hex[:6]}",
        description=None,
        connection_id=conn.id,
        created_by=owner.id,
        target={"table": "EMPLOYEES"},
    )
    suite.column_policy = policy
    db.commit()
    return suite


def _aggregate_check(db: Any, suite: Any, aggregate: str = "max") -> Check:
    check = Check(
        suite_id=suite.id,
        name=f"salary_{aggregate}",
        kind="aggregate",
        expectation_type="monitor:aggregate",
        config={"aggregate": aggregate, "column": "SALARY", "max_value": 100},
    )
    db.add(check)
    db.flush()
    return check


def _result(db: Any, suite: Any, check: Check, *, value: float = SECRET, **extra: Any) -> Result:
    run = Run(
        suite_id=suite.id,
        status="succeeded",
        triggered_by="manual",
        asset_id=suite.asset_id,
        finished_at=datetime.now(UTC),
    )
    db.add(run)
    db.flush()
    aggregate = check.config["aggregate"]
    fields: dict[str, Any] = {
        "observed_value": {"aggregate": aggregate, "column": "SALARY", "observed_value": value},
        "expected_value": {"monitor": "aggregate", "aggregate": aggregate, "column": "SALARY"},
    }
    fields.update(extra)
    result = Result(
        run_id=run.id, check_id=check.id, status="fail", metric_value=Decimal(str(value)), **fields
    )
    db.add(result)
    db.commit()
    return result


@pytest.fixture
def masked(db_session: Any) -> dict[str, Any]:
    """A max aggregate over a `pii_columns` column, one failing result."""
    owner = _owner(db_session)
    suite = _suite(db_session, owner, policy={"pii_columns": ["SALARY"]})
    check = _aggregate_check(db_session, suite)
    result = _result(db_session, suite, check)
    return {"owner": owner, "suite": suite, "check": check, "result": result}


def _as_rest(user: User) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


def _as_mcp(monkeypatch: Any, db_session: Any, user: User) -> None:
    monkeypatch.setattr(server, "get_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(server, "resolve_current_user", lambda _session: user)


# ── REST ───────────────────────────────────────────────────────────────────────


def test_get_run_withholds_the_cell(client: TestClient, masked: dict[str, Any]) -> None:
    _as_rest(masked["owner"])
    body = client.get(f"/api/v1/runs/{masked['result'].run_id}").json()
    row = body["results"][0]
    assert row["metric_value"] is None
    assert row["metric_value_redaction"] == "policy"
    assert SECRET_TEXT not in json.dumps(body)


def test_get_run_shows_a_mean_on_the_same_column(client: TestClient, db_session: Any) -> None:
    owner = _owner(db_session)
    suite = _suite(db_session, owner, policy={"pii_columns": ["SALARY"]})
    result = _result(db_session, suite, _aggregate_check(db_session, suite, "mean"))
    _as_rest(owner)
    row = client.get(f"/api/v1/runs/{result.run_id}").json()["results"][0]
    assert row["metric_value"] == SECRET
    assert row["metric_value_redaction"] is None


def test_check_history_withholds_the_cell(client: TestClient, masked: dict[str, Any]) -> None:
    _as_rest(masked["owner"])
    suite, check = masked["suite"], masked["check"]
    body = client.get(f"/api/v1/suites/{suite.id}/checks/{check.id}/history").json()
    assert [(p["metric_value"], p["metric_value_redaction"]) for p in body] == [(None, "policy")]
    assert SECRET_TEXT not in json.dumps(body)


def test_check_history_judges_each_point_by_the_check_as_of_it(db_session: Any) -> None:
    """The check measured MAX when the result was written and was edited to MEAN since; the
    result names no function itself, so only the version history knows it was a cell.
    """
    owner = _owner(db_session)
    suite = _suite(db_session, owner, policy={"pii_columns": ["SALARY"]})
    check = _aggregate_check(db_session, suite, "mean")
    t0 = datetime.now(UTC) - timedelta(days=1)
    for version_no, aggregate, at in (
        (1, "max", t0 - timedelta(hours=1)),
        (2, "mean", t0 + timedelta(hours=1)),
    ):
        db_session.add(
            CheckVersion(
                check_id=check.id,
                version_no=version_no,
                name=check.name,
                kind="aggregate",
                expectation_type="monitor:aggregate",
                config={"aggregate": aggregate, "column": "SALARY", "max_value": 100},
                created_at=at,
            )
        )
    _result(db_session, suite, check, observed_value=None, expected_value=None, created_at=t0)

    points = check_service.list_check_result_history(db_session, suite.id, check.id)

    assert [(p.metric_value, p.metric_value_redaction) for p in points] == [(None, "policy")]


def test_check_history_shows_a_mean_its_version_history_names(db_session: Any) -> None:
    """The other direction: the version history names a computed statistic, so the value shows
    even though the result itself names no function (no blanket fail-closed)."""
    owner = _owner(db_session)
    suite = _suite(db_session, owner, policy={"pii_columns": ["SALARY"]})
    check = _aggregate_check(db_session, suite, "mean")
    _result(db_session, suite, check, observed_value=None, expected_value=None)

    points = check_service.list_check_result_history(db_session, suite.id, check.id)

    assert [(p.metric_value, p.metric_value_redaction) for p in points] == [(SECRET, None)]


def test_run_comparison_withholds_both_cells_and_their_delta(
    client: TestClient, masked: dict[str, Any], db_session: Any
) -> None:
    other = _result(db_session, masked["suite"], masked["check"], value=123456.78)
    _as_rest(masked["owner"])
    body = client.get(f"/api/v1/runs/{masked['result'].run_id}/compare/{other.run_id}").json()
    row = body["rows"][0]
    assert row["base"]["metric_value"] is None and row["target"]["metric_value"] is None
    assert row["base"]["metric_value_redaction"] == "policy"
    assert row["delta"]["metric_delta"] is None and row["delta"]["metric_delta_pct"] is None
    assert row["delta"]["metric_changed"] is True
    serialized = json.dumps(body)
    assert SECRET_TEXT not in serialized and "123456.78" not in serialized


def test_rest_dry_run_withholds_the_cell_when_the_probe_masks(
    client: TestClient, db_session: Any, monkeypatch: Any
) -> None:
    owner = _owner(db_session)
    suite = _suite(
        db_session, owner, policy={"pii_columns": ["SALARY"], "require_classification": True}
    )
    monkeypatch.setattr(dryrun_service, "dry_run_check", lambda *_a, **_k: _dry_run_outcome())
    _as_rest(owner)
    body = client.post(
        f"/api/v1/suites/{suite.id}/checks/dryrun",
        json={
            "kind": "aggregate",
            "expectation_type": "monitor:aggregate",
            "config": {"aggregate": "max", "column": "SALARY", "max_value": 100},
        },
    ).json()
    assert body["metric_value"] is None
    assert body["metric_value_redaction"] == "policy"
    assert SECRET_TEXT not in json.dumps(body)


def _dry_run_outcome() -> dryrun_service.DryRunOutcome:
    return dryrun_service.DryRunOutcome(
        status="fail",
        metric_value=Decimal(SECRET_TEXT),
        observed_value={"aggregate": "max", "column": "SALARY", "observed_value": SECRET},
        expected_value={"monitor": "aggregate", "aggregate": "max", "column": "SALARY"},
    )


# ── MCP ────────────────────────────────────────────────────────────────────────


def test_mcp_get_run_results_withholds_the_cell(
    db_session: Any, monkeypatch: Any, masked: dict[str, Any]
) -> None:
    _as_mcp(monkeypatch, db_session, masked["owner"])
    out = server.get_run_results(str(masked["result"].run_id))
    row = out["checks"][0]
    assert row["metric_value"] is None
    assert row["metric_value_redaction"] == "policy"
    assert SECRET_TEXT not in json.dumps(out, default=str)


def test_mcp_get_check_history_withholds_the_cell(
    db_session: Any, monkeypatch: Any, masked: dict[str, Any]
) -> None:
    _as_mcp(monkeypatch, db_session, masked["owner"])
    out = server.get_check_history(str(masked["suite"].id), str(masked["check"].id))
    assert [(p["metric_value"], p["metric_value_redaction"]) for p in out["points"]] == [
        (None, "policy")
    ]


def test_mcp_dryrun_check_withholds_the_cell(
    db_session: Any, monkeypatch: Any, masked: dict[str, Any]
) -> None:
    monkeypatch.setattr(dryrun_service, "dry_run_check", lambda *_a, **_k: _dry_run_outcome())
    _as_mcp(monkeypatch, db_session, masked["owner"])
    out = server.dryrun_check(
        str(masked["suite"].id),
        kind="aggregate",
        expectation_type="monitor:aggregate",
        config={"aggregate": "max", "column": "SALARY", "max_value": 100},
    )
    assert out["metric_value"] is None
    assert out["metric_value_redaction"] == "policy"


# ── incident evidence ──────────────────────────────────────────────────────────


def _card(db: Any, masked: dict[str, Any]) -> dict[str, Any]:
    result = masked["result"]
    run = db.get(Run, result.run_id)
    asset = db.get(Asset, run.asset_id)
    return build_evidence(db, run=run, result=result, check=masked["check"], asset=asset)


def test_evidence_card_stores_the_failing_metric_and_its_trend_withheld(
    db_session: Any, masked: dict[str, Any]
) -> None:
    card = _card(db_session, masked)
    assert card["failing_result"]["metric_value"] is None
    assert card["failing_result"]["metric_value_redaction"] == "policy"
    assert [(p["metric_value"], p["metric_value_redaction"]) for p in card["metric_trend"]] == [
        (None, "policy")
    ]
    assert SECRET_TEXT not in json.dumps(card)


def test_evidence_card_withholds_a_sibling_cell_under_the_siblings_own_policy(
    db_session: Any, masked: dict[str, Any]
) -> None:
    """A max on the SAME asset from a DIFFERENT suite, whose own policy names the column."""
    owner = masked["owner"]
    other_suite = _suite(db_session, owner, policy={"pii_columns": ["SALARY"]})
    other_suite.asset_id = masked["suite"].asset_id
    db_session.commit()
    sibling = _aggregate_check(db_session, other_suite)
    _result(db_session, other_suite, sibling, value=555444.33)
    # The incident's own suite names nothing, so only the sibling's policy can mask it.
    masked["suite"].column_policy = None
    db_session.commit()

    card = _card(db_session, masked)

    entry = next(e for e in card["same_asset_siblings"] if e["check_id"] == str(sibling.id))
    assert entry["metric_value"] is None and entry["metric_value_redaction"] == "policy"
    assert "555444.33" not in json.dumps(card)


def _stored_incident(db: Any, masked: dict[str, Any]) -> Incident:
    """An incident whose evidence predates the fix: the raw cell on disk in every layer."""
    result = masked["result"]
    raw = {
        "failing_result": {
            "status": "fail",
            "metric_value": SECRET,
            "observed_value": result.observed_value,
            "expected_value": result.expected_value,
        },
        "metric_trend": [
            {
                "status": "fail",
                "metric_value": SECRET,
                "created_at": result.created_at.isoformat(),
                "run_id": str(result.run_id),
            }
        ],
    }
    incident = Incident(
        asset_id=masked["suite"].asset_id,
        check_id=masked["check"].id,
        suite_id=masked["suite"].id,
        status="open",
        occurrence_count=1,
        last_seen_at=result.created_at,
        evidence=raw,
    )
    db.add(incident)
    db.commit()
    return incident


def test_stored_evidence_is_withheld_when_read(db_session: Any, masked: dict[str, Any]) -> None:
    incident = _stored_incident(db_session, masked)
    evidence = incident_service.evidence_for_caller(
        db_session, incident, user_id=masked["owner"].id
    )
    assert evidence is not None
    assert evidence["failing_result"]["metric_value"] is None
    assert evidence["metric_trend"][0]["metric_value"] is None
    assert SECRET_TEXT not in json.dumps(evidence)


def test_get_incident_over_rest_withholds_the_cell(
    client: TestClient, db_session: Any, masked: dict[str, Any]
) -> None:
    incident = _stored_incident(db_session, masked)
    _as_rest(masked["owner"])
    body = client.get(f"/api/v1/incidents/{incident.id}").json()
    assert body["evidence"]["failing_result"]["metric_value_redaction"] == "policy"
    assert SECRET_TEXT not in json.dumps(body)


def test_stale_evidence_backfill_rewrites_the_cell_on_disk(
    db_session: Any, masked: dict[str, Any]
) -> None:
    incident = _stored_incident(db_session, masked)
    assert incident_service.redact_stale_evidence(db_session) == 1
    db_session.refresh(incident)
    assert SECRET_TEXT not in json.dumps(incident.evidence)
    assert incident_service.redact_stale_evidence(db_session) == 0


def test_rca_prompt_carries_neither_the_cell_nor_a_missing_metric_reading(
    db_session: Any, masked: dict[str, Any]
) -> None:
    incident = _stored_incident(db_session, masked)
    evidence = incident_service.evidence_for_caller(
        db_session, incident, user_id=masked["owner"].id
    )
    assert evidence is not None
    history = check_service.list_check_result_history(
        db_session, masked["suite"].id, masked["check"].id
    )
    prompt = llm_rca._render_evidence(evidence, history, [])
    assert SECRET_TEXT not in prompt
    assert "withheld, not missing" in prompt
    history_line = next(line for line in prompt.splitlines() if line.startswith("check_history"))
    assert '"metric_value_redaction": "policy"' in history_line


# ── alerts ─────────────────────────────────────────────────────────────────────


def test_alert_report_and_webhook_payload_withhold_the_cell(
    db_session: Any, masked: dict[str, Any]
) -> None:
    run = db_session.get(Run, masked["result"].run_id)
    report = builder.build_run_report(db_session, run)
    check_report = report.checks[0]
    assert check_report.metric_value is None
    assert check_report.metric_value_redaction == "policy"
    payload = webhook._check_payload(check_report)
    assert payload["metric_value"] is None and payload["metric_value_redaction"] == "policy"
    assert SECRET_TEXT not in json.dumps(payload, default=str)
