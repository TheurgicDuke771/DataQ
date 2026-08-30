"""Cadence-aware check suggestions (#1648): trigger-binding cadence context,
the monitor:freshness suggestion path, and orchestration coverage warnings.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from backend.app.db.models import (
    Connection,
    LlmInvocation,
    PipelineRun,
    Suite,
    TriggerBinding,
    User,
)
from backend.app.llm.base import LLMOutputInvalidError
from backend.app.services import llm_checksuggest
from backend.app.services import profile_service as profile_service_module
from backend.app.services.profile_service import ColumnProfile, ProfileResult
from backend.tests.support.fake_secret_store import FakeSecretStore
from backend.tests.support.llm_helpers import admin_user, make_sql_suite

NOW = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)


@pytest.fixture
def admin(db_session: Any) -> User:
    return admin_user(db_session, prefix="cadence")


def _invocation(db_session: Any, suite: Suite, admin: User) -> LlmInvocation:
    invocation = LlmInvocation(
        kind=llm_checksuggest.CHECKSUGGEST_KIND, requested_by_user_id=admin.id, suite_id=suite.id
    )
    db_session.add(invocation)
    db_session.commit()
    return invocation


def _binding(
    db_session: Any, suite: Suite, *, env: str = "dev", enabled: bool = True
) -> TriggerBinding:
    binding = TriggerBinding(
        provider="airflow",
        pipeline_or_dag_id="load_orders",
        env=env,
        suite_id=suite.id,
        enabled=enabled,
    )
    db_session.add(binding)
    db_session.commit()
    return binding


def _succeeded_run(
    db_session: Any, conn: Connection, *, hours_ago: float, env: str = "dev"
) -> None:
    started = NOW - timedelta(hours=hours_ago)
    db_session.add(
        PipelineRun(
            provider="airflow",
            connection_id=conn.id,
            provider_run_id=f"run-{uuid.uuid4().hex[:8]}",
            pipeline_or_dag_id="load_orders",
            env=env,
            status="succeeded",
            started_at=started,
            finished_at=started + timedelta(minutes=2),
            created_at=started,
        )
    )


def _orchestration_connection(db_session: Any, admin: User) -> Connection:
    conn = Connection(
        name=f"orch-{uuid.uuid4().hex[:6]}",
        type="airflow",
        env="dev",
        config={},
        created_by=admin.id,
    )
    db_session.add(conn)
    db_session.commit()
    return conn


def _empty_profile(column: str) -> ProfileResult:
    return ProfileResult(
        row_count=1,
        columns=[
            ColumnProfile(
                column=column,
                null_count=0,
                null_fraction=0.0,
                distinct_count=1,
                min_value=None,
                max_value=None,
                top_values=[],
            )
        ],
    )


def _mock_profile(monkeypatch: pytest.MonkeyPatch, column: str = "EMAIL") -> None:
    monkeypatch.setattr(profile_service_module, "list_columns", lambda *_a, **_kw: [column])
    monkeypatch.setattr(
        profile_service_module, "profile_connection", lambda *_a, **_kw: _empty_profile(column)
    )


# ── build_prompt: cadence context ────────────────────────────────────────────


def test_no_binding_offers_no_freshness_type(
    db_session: Any, admin: User, monkeypatch: Any
) -> None:
    suite = make_sql_suite(db_session, admin)
    _mock_profile(monkeypatch)
    invocation = _invocation(db_session, suite, admin)

    prompt, system, schema = llm_checksuggest.build_prompt(
        db_session, invocation, FakeSecretStore()
    )

    assert "Pipeline cadence" not in prompt
    assert (
        llm_checksuggest.FRESHNESS_EXPECTATION_TYPE
        not in schema["properties"]["suggestions"]["items"]["properties"]["expectation_type"][
            "enum"
        ]
    )
    assert system is not None and "freshness" not in system.lower()


def test_binding_with_insufficient_history_stays_freshness_free(
    db_session: Any, admin: User, monkeypatch: Any
) -> None:
    suite = make_sql_suite(db_session, admin)
    orch_conn = _orchestration_connection(db_session, admin)
    _binding(db_session, suite)
    _succeeded_run(db_session, orch_conn, hours_ago=1)  # only 1 run — below the minimum
    db_session.commit()
    _mock_profile(monkeypatch)
    invocation = _invocation(db_session, suite, admin)

    prompt, _system, schema = llm_checksuggest.build_prompt(
        db_session, invocation, FakeSecretStore()
    )

    assert "insufficient history" in prompt
    assert (
        llm_checksuggest.FRESHNESS_EXPECTATION_TYPE
        not in schema["properties"]["suggestions"]["items"]["properties"]["expectation_type"][
            "enum"
        ]
    )


def test_binding_with_regular_cadence_offers_freshness_and_states_the_gap(
    db_session: Any, admin: User, monkeypatch: Any
) -> None:
    suite = make_sql_suite(db_session, admin)
    orch_conn = _orchestration_connection(db_session, admin)
    _binding(db_session, suite)
    for hours_ago in (12, 8, 4, 0):
        _succeeded_run(db_session, orch_conn, hours_ago=hours_ago)
    db_session.commit()
    _mock_profile(monkeypatch)
    invocation = _invocation(db_session, suite, admin)

    prompt, system, schema = llm_checksuggest.build_prompt(
        db_session, invocation, FakeSecretStore()
    )

    assert "median=4.0h" in prompt
    assert (
        llm_checksuggest.FRESHNESS_EXPECTATION_TYPE
        in schema["properties"]["suggestions"]["items"]["properties"]["expectation_type"]["enum"]
    )
    assert system is not None and llm_checksuggest.FRESHNESS_EXPECTATION_TYPE in system


def test_a_disabled_binding_is_not_used(db_session: Any, admin: User, monkeypatch: Any) -> None:
    suite = make_sql_suite(db_session, admin)
    orch_conn = _orchestration_connection(db_session, admin)
    _binding(db_session, suite, enabled=False)
    for hours_ago in (12, 8, 4, 0):
        _succeeded_run(db_session, orch_conn, hours_ago=hours_ago)
    db_session.commit()
    _mock_profile(monkeypatch)
    invocation = _invocation(db_session, suite, admin)

    prompt, _system, _schema = llm_checksuggest.build_prompt(
        db_session, invocation, FakeSecretStore()
    )

    assert "Pipeline cadence" not in prompt


# ── output gate: monitor:freshness suggestions ───────────────────────────────


def _freshness_suggestion(**overrides: Any) -> dict[str, Any]:
    base = {
        "expectation_type": llm_checksuggest.FRESHNESS_EXPECTATION_TYPE,
        "name": "orders freshness",
        "rationale": "cadence shows ~4h between loads",
        "config": {"column": "LOADED_AT"},
        "fail_threshold_hours": 8,
    }
    base.update(overrides)
    return base


def test_output_gate_accepts_a_valid_freshness_suggestion(db_session: Any, admin: User) -> None:
    suite = make_sql_suite(db_session, admin)
    invocation = _invocation(db_session, suite, admin)

    out = llm_checksuggest.validate_output(
        db_session, invocation, {"suggestions": [_freshness_suggestion()]}
    )

    assert len(out["suggestions"]) == 1
    accepted = out["suggestions"][0]
    assert accepted["expectation_type"] == "monitor:freshness"
    assert accepted["dimension"] == "timeliness"
    assert accepted["fail_threshold_hours"] == 8.0


def test_output_gate_rejects_a_freshness_suggestion_with_no_positive_threshold(
    db_session: Any, admin: User
) -> None:
    """Alongside a valid GX suggestion, so the batch survives and the freshness
    rejection reason is inspectable (a lone rejection would hard-fail instead).
    """
    suite = make_sql_suite(db_session, admin)
    invocation = _invocation(db_session, suite, admin)
    good = {
        "expectation_type": "expect_column_values_to_not_be_null",
        "name": "n",
        "rationale": "r",
        "config": {"column": "EMAIL"},
    }

    out = llm_checksuggest.validate_output(
        db_session,
        invocation,
        {"suggestions": [good, _freshness_suggestion(fail_threshold_hours=None)]},
    )

    assert len(out["suggestions"]) == 1
    assert len(out["rejected"]) == 1
    assert "threshold" in out["rejected"][0]["reason"]


def test_output_gate_rejects_a_freshness_suggestion_missing_a_column(
    db_session: Any, admin: User
) -> None:
    suite = make_sql_suite(db_session, admin)  # snowflake — needs a column, no arrival-time
    invocation = _invocation(db_session, suite, admin)

    with pytest.raises(LLMOutputInvalidError):
        llm_checksuggest.validate_output(
            db_session, invocation, {"suggestions": [_freshness_suggestion(config={})]}
        )


def test_output_gate_rejects_a_negative_freshness_threshold(db_session: Any, admin: User) -> None:
    suite = make_sql_suite(db_session, admin)
    invocation = _invocation(db_session, suite, admin)
    good = {
        "expectation_type": "expect_column_values_to_not_be_null",
        "name": "n",
        "rationale": "r",
        "config": {"column": "EMAIL"},
    }

    out = llm_checksuggest.validate_output(
        db_session,
        invocation,
        {"suggestions": [good, _freshness_suggestion(fail_threshold_hours=-1)]},
    )

    assert len(out["suggestions"]) == 1
    assert len(out["rejected"]) == 1


# ── coverage_warnings: deterministic, independent of what the model returns ──


def test_coverage_warnings_is_empty_with_no_near_miss(db_session: Any, admin: User) -> None:
    suite = make_sql_suite(db_session, admin)
    invocation = _invocation(db_session, suite, admin)

    out = llm_checksuggest.validate_output(
        db_session,
        invocation,
        {"suggestions": [_freshness_suggestion()]},
    )

    assert out["coverage_warnings"] == []


def test_coverage_warnings_surfaces_a_real_near_miss(db_session: Any, admin: User) -> None:
    """A binding scoped to 'dev' whose pipeline ALSO succeeds in 'qa' (no binding
    there) is exactly the #1199 near-miss shape (env mismatch) reused as a
    coverage warning for this suite.
    """
    suite = make_sql_suite(db_session, admin)
    orch_conn = _orchestration_connection(db_session, admin)
    _binding(db_session, suite, env="dev")
    _succeeded_run(db_session, orch_conn, hours_ago=1, env="qa")
    db_session.commit()
    from backend.app.services import workspace_health_service

    workspace_health_service.record_trigger_binding_env_near_miss(
        db_session,
        provider="airflow",
        pipeline_or_dag_id="load_orders",
        run_env="qa",
        binding_env="dev",
    )
    invocation = _invocation(db_session, suite, admin)

    out = llm_checksuggest.validate_output(
        db_session, invocation, {"suggestions": [_freshness_suggestion()]}
    )

    assert len(out["coverage_warnings"]) == 1
    warning = out["coverage_warnings"][0]
    assert warning["run_env"] == "qa"
    assert warning["binding_env"] == "dev"
