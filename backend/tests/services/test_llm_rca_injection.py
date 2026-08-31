"""Prompt-injection adversarial battery for rca_narrative (#1633, extends #1632's
posture to the third LLM kind): a hostile string reaches the prompt as inert
data, the module never touches a raw sample row, and a response is never
logged unredacted.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import structlog
from sqlalchemy import select
from structlog.testing import capture_logs

from backend.app.db.models import Check, Connection, Incident, LlmInvocation, Result, Run, User
from backend.app.llm.base import LLMResult
from backend.app.services import incident_service, llm_rca, llm_service, suite_service
from backend.tests.support.adversarial import PROMPT_INJECTION_STRINGS
from backend.tests.support.fake_secret_store import FakeSecretStore
from backend.tests.support.llm_helpers import admin_user

_SF_CONFIG = {"account": "ab12345.eu-west-1", "database": "ANALYTICS", "schema": "PUBLIC"}


def _breach_with_check_name(db: Any, owner: User, name: str) -> Any:
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
    suite = suite_service.create_suite(
        db,
        name=f"s-{uuid.uuid4().hex[:6]}",
        description=None,
        connection_id=conn.id,
        created_by=owner.id,
        target={"table": "ORDERS"},
    )
    check = Check(
        suite_id=suite.id,
        name=name,
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
    incident = db.scalars(
        select(Incident).where(Incident.suite_id == suite.id).order_by(Incident.created_at.desc())
    ).first()
    assert incident is not None
    return incident


def _invocation(db: Any, incident: Any, requester: User) -> LlmInvocation:
    invocation = LlmInvocation(
        kind=llm_rca.RCA_KIND,
        requested_by_user_id=requester.id,
        suite_id=incident.suite_id,
        request={"incident_id": str(incident.id)},
    )
    db.add(invocation)
    db.commit()
    return invocation


@pytest.mark.parametrize("injection", PROMPT_INJECTION_STRINGS)
def test_injection_in_check_name_reaches_the_prompt_as_data_only(
    db_session: Any, injection: str
) -> None:
    owner = admin_user(db_session, prefix="rca-inj")
    incident = _breach_with_check_name(db_session, owner, injection)
    invocation = _invocation(db_session, incident, owner)

    prompt, _system, _schema = llm_rca.build_prompt(db_session, invocation, FakeSecretStore())
    assert injection in prompt


# ── PII discipline: structural, not incidental ───────────────────────────────


def test_module_touches_no_raw_sample_row() -> None:
    """No raw failing-sample row can reach a prompt if `Result` is never even
    imported — a structural guarantee, not an incidental one (the #849 lesson:
    test the pipeline, not a scrub helper that could be bypassed). Same
    guarantee `test_llm_injection_battery.py` proves for the other two kinds.
    """
    import inspect

    assert "sample_failures" not in inspect.getsource(llm_rca)
    assert not hasattr(llm_rca, "Result")


# ── outbound: the response is untrusted too ──────────────────────────────────


class _StructuredProvider:
    model = "fake"

    def __init__(self, parsed: dict[str, Any]) -> None:
        self._parsed = parsed

    def complete(self, prompt: str, **_kw: Any) -> LLMResult:  # pragma: no cover - unused
        return LLMResult(text=str(self._parsed))

    def complete_structured(self, prompt: str, *, schema: dict[str, Any], **_kw: Any) -> LLMResult:
        return LLMResult(text="", parsed=self._parsed, input_tokens=3, output_tokens=1)


def test_the_response_is_never_logged_unredacted(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = f"INJECTION-MARKER-{uuid.uuid4().hex}"
    owner = admin_user(db_session, prefix="rca-log")
    incident = _breach_with_check_name(db_session, owner, "orders_not_null")
    invocation = _invocation(db_session, incident, owner)
    payload = {
        "summary": marker,
        "ranked_hypotheses": [
            {"cause": marker, "confidence": "low", "evidence_refs": ["failing_result"]}
        ],
    }
    monkeypatch.setattr(
        llm_service, "build_provider", lambda *_a, **_kw: _StructuredProvider(payload)
    )

    with capture_logs() as logs:
        monkeypatch.setattr(
            llm_service, "log", structlog.get_logger("backend.app.services.llm_service")
        )
        status = llm_service.execute_invocation(
            db_session, invocation.id, secret_store=FakeSecretStore()
        )

    assert status == "succeeded"
    db_session.refresh(invocation)
    assert marker in str(invocation.response)  # sanity: the response was actually used
    assert logs, "no log calls captured — the assertion above would be vacuous"
    assert marker not in str(logs)
