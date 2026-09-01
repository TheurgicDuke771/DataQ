"""LLM root-cause narrative — Layer 2 on the evidence card (ADR 0042, #1633)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
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


def test_validate_output_all_rejected_names_the_reasons(
    db_session: Any, world: dict[str, Any]
) -> None:
    """#1781: `_valid_hypothesis` used to return bare `None` on rejection —
    zero reason captured at all, worse than `llm_checksuggest`'s own pre-#1780
    state (which at least built a `rejected` list, just discarded it on the
    exception path). Every rejection reason must now be readable, both folded
    into the message and structured under `exc.detail`.
    """
    invocation = _invocation(db_session, world["incident"], world["owner"])
    payload = _valid_payload(
        ranked_hypotheses=[
            {"cause": "x", "confidence": "certain", "evidence_refs": ["metric_trend"]},
            {"cause": "y", "confidence": "high", "evidence_refs": ["sample_failures"]},
            "not even an object",
        ]
    )
    with pytest.raises(LLMOutputInvalidError) as exc_info:
        llm_rca.validate_output(db_session, invocation, payload)
    exc = exc_info.value
    assert "3 rejected" in str(exc)
    assert "confidence must be one of" in str(exc)
    assert "closed vocabulary" in str(exc)
    assert "hypothesis was not an object" in str(exc)
    assert exc.detail["rejected_count"] == 3
    assert len(exc.detail["rejected"]) == 3
    assert exc.detail["truncated"] is False  # 3 rejected, all 3 shown — nothing cut


def test_validate_output_all_rejected_flags_truncation_past_max_hypotheses(
    db_session: Any, world: dict[str, Any]
) -> None:
    """`rejected` is not bounded by `_MAX_HYPOTHESES` the way the accepted list
    is — a non-compliant provider can send more raw hypotheses than the
    schema's own maxItems allows.
    """
    invocation = _invocation(db_session, world["incident"], world["owner"])
    overflow = 2
    payload = _valid_payload(
        ranked_hypotheses=["not even an object"] * (llm_rca._MAX_HYPOTHESES + overflow)
    )
    with pytest.raises(LLMOutputInvalidError) as exc_info:
        llm_rca.validate_output(db_session, invocation, payload)
    exc = exc_info.value
    assert exc.detail["rejected_count"] == llm_rca._MAX_HYPOTHESES + overflow
    assert len(exc.detail["rejected"]) == llm_rca._MAX_HYPOTHESES
    assert exc.detail["truncated"] is True
    assert f"+{overflow} more" in str(exc)


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


def test_validate_output_keeps_a_valid_hypothesis_past_the_cap_over_earlier_invalid_ones(
    db_session: Any, world: dict[str, Any]
) -> None:
    """The whole list is filtered BEFORE capping to `_MAX_HYPOTHESES` — slicing
    first could discard a valid, grounded hypothesis in favor of keeping
    earlier malformed ones (not every provider enforces its own schema's
    `maxItems` server-side, the `llm_checksuggest` precedent).
    """
    invocation = _invocation(db_session, world["incident"], world["owner"])
    invalid = [
        {"cause": "no refs", "confidence": "low", "evidence_refs": []}
        for _ in range(llm_rca._MAX_HYPOTHESES)
    ]
    valid_one = {
        "cause": "the one that survives",
        "confidence": "high",
        "evidence_refs": ["failing_result"],
    }
    payload = _valid_payload(ranked_hypotheses=[*invalid, valid_one])
    result = llm_rca.validate_output(db_session, invocation, payload)
    assert [h["cause"] for h in result["ranked_hypotheses"]] == ["the one that survives"]


# ── evidence_context caching (#1633 review) ─────────────────────────────────


def test_evidence_context_is_computed_once_across_build_and_validate(
    db_session: Any, world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`build_prompt` and `validate_output` share one snapshot per invocation —
    a concurrent run landing between the two (the LLM round-trip can take
    seconds) must not make the persisted `blind_spots` describe evidence the
    model never actually saw.
    """
    invocation = _invocation(db_session, world["incident"], world["owner"])
    calls = {"n": 0}
    real_check_history = llm_rca._check_history

    def _counting_check_history(session: Any, incident: Any) -> list[Any]:
        calls["n"] += 1
        return real_check_history(session, incident)

    monkeypatch.setattr(llm_rca, "_check_history", _counting_check_history)

    llm_rca.build_prompt(db_session, invocation, FakeSecretStore())
    llm_rca.validate_output(db_session, invocation, _valid_payload())
    assert calls["n"] == 1


def test_evidence_context_cache_is_per_invocation_not_global(
    db_session: Any, world: dict[str, Any]
) -> None:
    """The cache lives on the invocation object, not shared module state — a
    second, unrelated invocation must recompute its own context.
    """
    first = _invocation(db_session, world["incident"], world["owner"])
    llm_rca.build_prompt(db_session, first, FakeSecretStore())
    assert getattr(first, llm_rca._EVIDENCE_CONTEXT_ATTR) is not None

    second = _invocation(db_session, world["incident"], world["owner"])
    assert getattr(second, llm_rca._EVIDENCE_CONTEXT_ATTR, None) is None


# ── shared constants (#1633 review) ─────────────────────────────────────────


def test_monitor_kinds_is_the_shared_incident_evidence_constant() -> None:
    """A locally-duplicated copy would silently drift from
    `incident_evidence._kind_detail_layer`'s own dispatch if a new monitor
    kind were ever added there without also updating a second copy here.
    """
    from backend.app.services import incident_evidence

    assert getattr(llm_rca, "MONITOR_KINDS") is incident_evidence.MONITOR_KINDS  # noqa: B009


# ── prompt size caps (#1633 review) ─────────────────────────────────────────


def test_render_evidence_caps_sibling_checks_and_notes_the_omission() -> None:
    siblings = [{"check_name": f"check_{i}", "status": "pass"} for i in range(30)]
    evidence = {"sibling_checks": siblings}
    prompt = llm_rca._render_evidence(evidence, [], [])
    assert "check_0" in prompt
    assert f"check_{llm_rca._MAX_RENDERED_ITEMS}" not in prompt  # first omitted entry
    assert "10 more not shown" in prompt


def test_render_evidence_caps_downstream_blast_radius_and_notes_the_omission() -> None:
    blast = [{"name": f"asset_{i}"} for i in range(25)]
    evidence = {"downstream_blast_radius": blast}
    prompt = llm_rca._render_evidence(evidence, [], [])
    assert "asset_0" in prompt
    assert f"asset_{llm_rca._MAX_RENDERED_ITEMS}" not in prompt
    assert "5 more not shown" in prompt


def test_render_evidence_omits_the_note_when_under_the_cap() -> None:
    evidence = {"sibling_checks": [{"check_name": "only_one", "status": "pass"}]}
    prompt = llm_rca._render_evidence(evidence, [], [])
    assert "more not shown" not in prompt


# ── latest_narrative_for_incident (#1647) ───────────────────────────────────


def test_latest_narrative_for_incident_none_when_never_generated(
    db_session: Any, world: dict[str, Any]
) -> None:
    assert llm_rca.latest_narrative_for_incident(db_session, world["incident"].id) is None


def test_latest_narrative_for_incident_returns_the_succeeded_response(
    db_session: Any, world: dict[str, Any]
) -> None:
    invocation = _invocation(db_session, world["incident"], world["owner"])
    invocation.status = "succeeded"
    invocation.response = {"summary": "grounded narrative", "ranked_hypotheses": []}
    db_session.commit()

    result = llm_rca.latest_narrative_for_incident(db_session, world["incident"].id)
    assert result == {"summary": "grounded narrative", "ranked_hypotheses": []}


def test_latest_narrative_for_incident_ignores_a_newer_failed_retry(
    db_session: Any, world: dict[str, Any]
) -> None:
    """Same shape as the running-invocation case: a failed retry must not
    shadow a still-valid older narrative just for being newer.
    """
    older = _invocation(db_session, world["incident"], world["owner"])
    older.status = "succeeded"
    older.response = {"summary": "the valid older narrative", "ranked_hypotheses": []}
    older.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    db_session.commit()

    newer = _invocation(db_session, world["incident"], world["owner"])
    newer.status = "failed"
    newer.response = None
    newer.error = "internal: boom"
    newer.created_at = datetime(2026, 1, 2, tzinfo=UTC)
    db_session.commit()

    result = llm_rca.latest_narrative_for_incident(db_session, world["incident"].id)
    assert result is not None
    assert result["summary"] == "the valid older narrative"


def test_latest_narrative_for_incident_ignores_a_newer_still_running_invocation(
    db_session: Any, world: dict[str, Any]
) -> None:
    """The dangerous case, not a solo pending row (which has `response=None`
    regardless of any status filter): an OLDER succeeded narrative exists, and
    someone has just re-triggered a NEWER one that hasn't finished yet. The
    ordering must not let the in-flight row's absent response shadow the
    still-valid older one.
    """
    older = _invocation(db_session, world["incident"], world["owner"])
    older.status = "succeeded"
    older.response = {"summary": "the valid older narrative", "ranked_hypotheses": []}
    older.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    db_session.commit()

    newer = _invocation(db_session, world["incident"], world["owner"])
    newer.status = "running"
    newer.response = None
    newer.created_at = datetime(2026, 1, 2, tzinfo=UTC)
    db_session.commit()

    result = llm_rca.latest_narrative_for_incident(db_session, world["incident"].id)
    assert result is not None
    assert result["summary"] == "the valid older narrative"


def test_latest_narrative_for_incident_is_scoped_to_the_right_incident(
    db_session: Any, world: dict[str, Any]
) -> None:
    other_check = _check(db_session, world["suite"], name="other_check")
    other_incident = _breach(db_session, world["suite"], other_check)
    other_invocation = _invocation(db_session, other_incident, world["owner"])
    other_invocation.status = "succeeded"
    other_invocation.response = {
        "summary": "belongs to a different incident",
        "ranked_hypotheses": [],
    }
    db_session.commit()

    assert llm_rca.latest_narrative_for_incident(db_session, world["incident"].id) is None
    result = llm_rca.latest_narrative_for_incident(db_session, other_incident.id)
    assert result is not None
    assert result["summary"] == "belongs to a different incident"


def test_latest_narrative_for_incident_returns_the_most_recent(
    db_session: Any, world: dict[str, Any]
) -> None:
    """Explicit `created_at`s: a test transaction's `now()` is transaction-start
    time, not per-statement, so two commits within it can share one timestamp.
    """
    older = _invocation(db_session, world["incident"], world["owner"])
    older.status = "succeeded"
    older.response = {"summary": "stale narrative", "ranked_hypotheses": []}
    older.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    db_session.commit()

    newer = _invocation(db_session, world["incident"], world["owner"])
    newer.status = "succeeded"
    newer.response = {"summary": "fresh narrative", "ranked_hypotheses": []}
    newer.created_at = datetime(2026, 1, 2, tzinfo=UTC)
    db_session.commit()

    result = llm_rca.latest_narrative_for_incident(db_session, world["incident"].id)
    assert result is not None
    assert result["summary"] == "fresh narrative"


def test_latest_narrative_for_incident_order_by_has_an_id_tiebreak(db_session: Any) -> None:
    """Structural, not outcome-based: two rows with an identical `created_at`
    return a STABLE physical order regardless of whether a tiebreak column is
    present (the #1648 lesson — repeated queries against unchanged data don't
    expose a missing ORDER BY term empirically), so this can only be proven by
    inspecting the compiled SQL.
    """
    from sqlalchemy import event

    statements: list[str] = []

    def _capture(_conn: Any, _cursor: Any, statement: str, *_a: Any) -> None:
        if statement.strip().upper().startswith("SELECT") and "llm_invocations" in statement:
            statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        llm_rca.latest_narrative_for_incident(db_session, uuid.uuid4())  # no row needed
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert statements, "no SELECT against llm_invocations was captured"
    order_by = statements[0].upper().split("ORDER BY", 1)[1]
    assert "CREATED_AT" in order_by
    assert "ID" in order_by


# ── latest_narrative_for_alert (#1647 review — cross-suite leak) ────────────


def test_latest_narrative_for_alert_returns_the_narrative_with_no_cross_suite_siblings(
    db_session: Any, world: dict[str, Any]
) -> None:
    invocation = _invocation(db_session, world["incident"], world["owner"])
    invocation.status = "succeeded"
    invocation.response = {"summary": "safe to surface", "ranked_hypotheses": []}
    db_session.commit()

    result = llm_rca.latest_narrative_for_alert(db_session, world["incident"])
    assert result is not None
    assert result["summary"] == "safe to surface"


def test_latest_narrative_for_alert_refuses_when_the_card_has_cross_suite_siblings(
    db_session: Any, world: dict[str, Any]
) -> None:
    """A narrative is free text generated from the REQUESTER's own grant-scoped
    view — it can legitimately name a cross-suite sibling's check. Alert
    recipients are a suite-scoped audience with no grant to redact prose
    against, so the whole narrative is withheld rather than risk a leak.
    """
    other_owner = _user(db_session, "other-owner-alert@example.com")
    other_conn = _connection(db_session, other_owner)
    other_suite = _suite(db_session, other_owner, other_conn, table="ORDERS")
    assert other_suite.asset_id == world["suite"].asset_id
    other_check = _check(db_session, other_suite, name="orders_volume_ok")
    other_run = Run(suite_id=other_suite.id, status="succeeded", asset_id=other_suite.asset_id)
    db_session.add(other_run)
    db_session.flush()
    db_session.add(Result(run_id=other_run.id, check_id=other_check.id, status="fail"))
    db_session.commit()

    incident = _breach(db_session, world["suite"], world["check"])
    assert incident.evidence is not None
    assert incident.evidence["same_asset_siblings"]  # non-empty — the gate condition

    invocation = _invocation(db_session, incident, world["owner"])
    invocation.status = "succeeded"
    invocation.response = {
        "summary": "mentions orders_volume_ok from the other suite",
        "ranked_hypotheses": [],
    }
    db_session.commit()

    assert llm_rca.latest_narrative_for_alert(db_session, incident) is None
    # The unfiltered lookup still finds it — proving the gate, not the query, withheld it.
    assert llm_rca.latest_narrative_for_incident(db_session, incident.id) is not None
