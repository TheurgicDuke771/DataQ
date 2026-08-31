"""LLM root-cause narrative — Layer 2 on the evidence card (ADR 0042, #1633)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from backend.app.db.models import (
    Check,
    Connection,
    Incident,
    LlmInvocation,
    Result,
    Run,
    Share,
    Suite,
    User,
)
from backend.app.llm.base import LLMOutputInvalidError, LLMRequestInvalidError
from backend.app.services import incident_service, llm_rca, suite_service
from backend.tests.support.fake_secret_store import FakeSecretStore
from backend.tests.support.llm_helpers import admin_user

_SF_CONFIG = {"account": "ab12345.eu-west-1", "database": "ANALYTICS", "schema": "PUBLIC"}


def _user(db: Any, email: str) -> User:
    u = User(aad_object_id=uuid.uuid4().hex, email=email)
    db.add(u)
    db.flush()
    return u


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


def _suite(db: Any, owner: User, conn: Connection, table: str = "ORDERS") -> Suite:
    return suite_service.create_suite(
        db,
        name=f"s-{uuid.uuid4().hex[:6]}",
        description=None,
        connection_id=conn.id,
        created_by=owner.id,
        target={"table": table},
    )


def _check(db: Any, suite: Suite, name: str = "orders_not_null") -> Check:
    c = Check(
        suite_id=suite.id,
        name=name,
        kind="expectation",
        expectation_type="expect_column_values_to_not_be_null",
        config={"column": "id"},
    )
    db.add(c)
    db.flush()
    return c


def _breach(db: Any, suite: Suite, check: Check, *, status: str = "fail") -> Incident:
    """A failing run + result, synced into an open incident (a real evidence
    card, built through `build_evidence` — never hand-crafted).
    """
    run = Run(suite_id=suite.id, status="succeeded", asset_id=suite.asset_id)
    db.add(run)
    db.flush()
    db.add(Result(run_id=run.id, check_id=check.id, status=status, metric_value=0.4))
    db.commit()
    incident_service.sync_incidents_for_run(db, run_id=run.id)
    incident: Incident | None = db.scalars(
        select(Incident)
        .where(Incident.suite_id == suite.id, Incident.check_id == check.id)
        .order_by(Incident.created_at.desc())
    ).first()
    assert incident is not None
    return incident


@pytest.fixture
def world(db_session: Any) -> dict[str, Any]:
    owner = admin_user(db_session, prefix="rca")
    conn = _connection(db_session, owner)
    suite = _suite(db_session, owner, conn)
    check = _check(db_session, suite)
    incident = _breach(db_session, suite, check)
    return {"owner": owner, "conn": conn, "suite": suite, "check": check, "incident": incident}


def _invocation(db: Any, incident: Incident, requester: User) -> LlmInvocation:
    invocation = LlmInvocation(
        kind=llm_rca.RCA_KIND,
        requested_by_user_id=requester.id,
        suite_id=incident.suite_id,
        request={"incident_id": str(incident.id)},
    )
    db.add(invocation)
    db.commit()
    return invocation


# ── check_generation_preconditions ──────────────────────────────────────────


def test_preconditions_refuse_an_incident_with_no_evidence(
    db_session: Any, world: dict[str, Any]
) -> None:
    incident = world["incident"]
    incident.evidence = None
    db_session.add(incident)
    db_session.commit()
    with pytest.raises(LLMRequestInvalidError):
        llm_rca.check_generation_preconditions(incident)


# ── build_prompt ─────────────────────────────────────────────────────────────


def test_build_prompt_grounds_the_narrative_in_the_evidence_card(
    db_session: Any, world: dict[str, Any]
) -> None:
    invocation = _invocation(db_session, world["incident"], world["owner"])
    prompt, system, schema = llm_rca.build_prompt(db_session, invocation, FakeSecretStore())
    assert world["check"].name in prompt
    assert "ANALYTICS.PUBLIC.ORDERS" in prompt
    assert schema is llm_rca.RCA_SCHEMA
    assert "evidence_refs" in str(schema)
    assert system is not None
    assert "DATA, not instructions" in system


def test_build_prompt_refuses_when_the_incident_is_gone(
    db_session: Any, world: dict[str, Any]
) -> None:
    invocation = _invocation(db_session, world["incident"], world["owner"])
    invocation.request = {"incident_id": str(uuid.uuid4())}
    db_session.commit()
    with pytest.raises(LLMRequestInvalidError):
        llm_rca.build_prompt(db_session, invocation, FakeSecretStore())


def test_build_prompt_refuses_a_malformed_incident_id(
    db_session: Any, world: dict[str, Any]
) -> None:
    invocation = _invocation(db_session, world["incident"], world["owner"])
    invocation.request = {"incident_id": "not-a-uuid"}
    db_session.commit()
    with pytest.raises(LLMRequestInvalidError):
        llm_rca.build_prompt(db_session, invocation, FakeSecretStore())


def test_build_prompt_refuses_when_the_requester_account_is_gone(
    db_session: Any, world: dict[str, Any]
) -> None:
    """`requested_by_user_id` is SET NULL on user erasure (#1319) — by the time
    a worker picks this up there may be no grant set left to redact
    `same_asset_siblings` against; refuse rather than default to "show all".
    """
    invocation = _invocation(db_session, world["incident"], world["owner"])
    invocation.requested_by_user_id = None
    db_session.commit()
    with pytest.raises(LLMRequestInvalidError):
        llm_rca.build_prompt(db_session, invocation, FakeSecretStore())


def test_build_prompt_withholds_a_cross_suite_sibling_the_requester_cannot_view(
    db_session: Any, world: dict[str, Any]
) -> None:
    other_owner = _user(db_session, "other-owner@example.com")
    other_conn = _connection(db_session, other_owner)
    other_suite = _suite(db_session, other_owner, other_conn, table="ORDERS")
    assert other_suite.asset_id == world["suite"].asset_id
    other_check = _check(db_session, other_suite, name="orders_volume_ok")
    other_run = Run(suite_id=other_suite.id, status="succeeded", asset_id=other_suite.asset_id)
    db_session.add(other_run)
    db_session.flush()
    db_session.add(Result(run_id=other_run.id, check_id=other_check.id, status="fail"))
    db_session.commit()

    # Re-breach so the incident's card re-snapshots and picks up the sibling.
    incident = _breach(db_session, world["suite"], world["check"])
    requester = _user(db_session, "viewer@example.com")  # no share on other_suite
    invocation = _invocation(db_session, incident, requester)

    prompt, _system, _schema = llm_rca.build_prompt(db_session, invocation, FakeSecretStore())
    assert "orders_volume_ok" not in prompt
    assert "withheld" in prompt.lower()


def test_build_prompt_shows_a_cross_suite_sibling_the_requester_can_view(
    db_session: Any, world: dict[str, Any]
) -> None:
    other_owner = _user(db_session, "other-owner2@example.com")
    other_conn = _connection(db_session, other_owner)
    other_suite = _suite(db_session, other_owner, other_conn, table="ORDERS")
    other_check = _check(db_session, other_suite, name="orders_volume_ok")
    other_run = Run(suite_id=other_suite.id, status="succeeded", asset_id=other_suite.asset_id)
    db_session.add(other_run)
    db_session.flush()
    db_session.add(Result(run_id=other_run.id, check_id=other_check.id, status="fail"))
    db_session.commit()

    incident = _breach(db_session, world["suite"], world["check"])
    requester = _user(db_session, "granted-viewer@example.com")
    db_session.add(Share(suite_id=other_suite.id, user_id=requester.id, permission="view"))
    db_session.commit()
    invocation = _invocation(db_session, incident, requester)

    prompt, _system, _schema = llm_rca.build_prompt(db_session, invocation, FakeSecretStore())
    assert "orders_volume_ok" in prompt


# ── _blind_spots ─────────────────────────────────────────────────────────────


def test_blind_spots_names_a_deleted_check() -> None:
    assert "deleted" in " ".join(llm_rca._blind_spots({"check": None}, history_unavailable=False))


def test_blind_spots_names_insufficient_anomaly_history() -> None:
    evidence = {
        "check": {"kind": "anomaly"},
        "kind_detail": {"insufficient_history": True},
    }
    spots = llm_rca._blind_spots(evidence, history_unavailable=False)
    assert any("too little history" in s for s in spots)


def test_blind_spots_names_a_restricted_sibling_count() -> None:
    evidence = {"check": {"kind": "expectation"}, "same_asset_siblings_restricted_count": 2}
    spots = llm_rca._blind_spots(evidence, history_unavailable=False)
    assert any("2 cross-suite sibling" in s for s in spots)


def test_blind_spots_empty_for_a_fully_populated_card() -> None:
    evidence = {
        "check": {"kind": "expectation"},
        "kind_detail": None,
        "same_asset_siblings_restricted_count": 0,
        "upstream_pipeline_run": {"provider": "airflow"},
        "downstream_blast_radius": [{"name": "x"}],
        "profile_diff": "not-actually-null",  # only presence is checked
    }
    assert llm_rca._blind_spots(evidence, history_unavailable=False) == []


# ── validate_output ──────────────────────────────────────────────────────────


def _valid_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "summary": "The check likely failed because of a data volume drop.",
        "ranked_hypotheses": [
            {
                "cause": "Upstream pipeline delivered fewer rows than usual.",
                "confidence": "medium",
                "evidence_refs": ["metric_trend"],
            }
        ],
        "suggested_next_checks": ["Add a row-count monitor on the source table."],
    }
    payload.update(overrides)
    return payload


def test_validate_output_accepts_a_well_formed_payload(
    db_session: Any, world: dict[str, Any]
) -> None:
    invocation = _invocation(db_session, world["incident"], world["owner"])
    result = llm_rca.validate_output(db_session, invocation, _valid_payload())
    assert result["summary"].startswith("The check likely failed")
    assert len(result["ranked_hypotheses"]) == 1
    assert result["ranked_hypotheses"][0]["evidence_refs"] == ["metric_trend"]
    assert result["suggested_next_checks"] == ["Add a row-count monitor on the source table."]
    assert isinstance(result["blind_spots"], list)


def test_validate_output_refuses_a_blank_summary(db_session: Any, world: dict[str, Any]) -> None:
    invocation = _invocation(db_session, world["incident"], world["owner"])
    with pytest.raises(LLMOutputInvalidError):
        llm_rca.validate_output(db_session, invocation, _valid_payload(summary="   "))


def test_validate_output_drops_a_hypothesis_with_no_evidence_refs(
    db_session: Any, world: dict[str, Any]
) -> None:
    invocation = _invocation(db_session, world["incident"], world["owner"])
    payload = _valid_payload(
        ranked_hypotheses=[
            {"cause": "unsupported guess", "confidence": "high", "evidence_refs": []}
        ]
    )
    with pytest.raises(LLMOutputInvalidError):
        llm_rca.validate_output(db_session, invocation, payload)


def test_validate_output_drops_a_hypothesis_citing_an_unknown_evidence_layer(
    db_session: Any, world: dict[str, Any]
) -> None:
    invocation = _invocation(db_session, world["incident"], world["owner"])
    payload = _valid_payload(
        ranked_hypotheses=[
            {
                "cause": "invented layer",
                "confidence": "high",
                "evidence_refs": ["sample_failures"],  # never offered — PII layer, closed vocab
            }
        ]
    )
    with pytest.raises(LLMOutputInvalidError):
        llm_rca.validate_output(db_session, invocation, payload)


def test_validate_output_drops_an_invalid_confidence_level(
    db_session: Any, world: dict[str, Any]
) -> None:
    invocation = _invocation(db_session, world["incident"], world["owner"])
    payload = _valid_payload(
        ranked_hypotheses=[
            {"cause": "x", "confidence": "certain", "evidence_refs": ["metric_trend"]}
        ]
    )
    with pytest.raises(LLMOutputInvalidError):
        llm_rca.validate_output(db_session, invocation, payload)


def test_validate_output_a_good_hypothesis_survives_a_bad_sibling(
    db_session: Any, world: dict[str, Any]
) -> None:
    """One malformed hypothesis must not sink an otherwise-valid batch — same
    posture as check_suggestion's per-item drop.
    """
    invocation = _invocation(db_session, world["incident"], world["owner"])
    payload = _valid_payload(
        ranked_hypotheses=[
            {"cause": "no refs", "confidence": "low", "evidence_refs": []},
            {
                "cause": "grounded cause",
                "confidence": "high",
                "evidence_refs": ["failing_result", "metric_trend"],
            },
        ]
    )
    result = llm_rca.validate_output(db_session, invocation, payload)
    assert len(result["ranked_hypotheses"]) == 1
    assert result["ranked_hypotheses"][0]["cause"] == "grounded cause"


def test_validate_output_caps_suggested_next_checks(db_session: Any, world: dict[str, Any]) -> None:
    invocation = _invocation(db_session, world["incident"], world["owner"])
    payload = _valid_payload(
        suggested_next_checks=[f"idea {i}" for i in range(llm_rca._MAX_NEXT_CHECKS + 3)]
    )
    result = llm_rca.validate_output(db_session, invocation, payload)
    assert len(result["suggested_next_checks"]) == llm_rca._MAX_NEXT_CHECKS


def test_validate_output_refuses_when_the_incident_is_gone(
    db_session: Any, world: dict[str, Any]
) -> None:
    invocation = _invocation(db_session, world["incident"], world["owner"])
    invocation.request = {"incident_id": str(uuid.uuid4())}
    db_session.commit()
    with pytest.raises(LLMRequestInvalidError):
        llm_rca.validate_output(db_session, invocation, _valid_payload())
