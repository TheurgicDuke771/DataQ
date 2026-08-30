"""Prompt-injection adversarial battery (#1632).

Posture: output validation is the security boundary, not prompt hygiene — the
per-feature output-gate tests already prove that half. This file proves a
hostile string reaches the prompt as inert data in every context slot, that
neither builder can read a sample row, and that a response is never logged
unredacted.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import structlog
from structlog.testing import capture_logs

from backend.app.db.models import LlmInvocation, User
from backend.app.llm.base import LLMResult
from backend.app.services import llm_checksuggest, llm_service, llm_sqlgen
from backend.app.services import profile_service as profile_service_module
from backend.app.services.profile_service import ColumnProfile, ProfileResult
from backend.tests.support.adversarial import PROMPT_INJECTION_STRINGS
from backend.tests.support.fake_secret_store import FakeSecretStore
from backend.tests.support.llm_helpers import admin_user, make_sql_suite

_KINDS = [llm_sqlgen.SQLGEN_KIND, llm_checksuggest.CHECKSUGGEST_KIND]


@pytest.fixture
def admin(db_session: Any) -> User:
    return admin_user(db_session, prefix="injection")


def _invocation(db_session: Any, kind: str, suite: Any, admin: User) -> LlmInvocation:
    invocation = LlmInvocation(
        kind=kind,
        requested_by_user_id=admin.id,
        suite_id=suite.id,
        request={"description": "no null emails"} if kind == llm_sqlgen.SQLGEN_KIND else {},
    )
    db_session.add(invocation)
    db_session.commit()
    return invocation


def _build_prompt(kind: str, db_session: Any, invocation: LlmInvocation, store: Any) -> str:
    builder = (
        llm_sqlgen.build_prompt if kind == llm_sqlgen.SQLGEN_KIND else llm_checksuggest.build_prompt
    )
    prompt, _system, _schema = builder(db_session, invocation, store)
    return prompt


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


# ── column name slot ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("injection", PROMPT_INJECTION_STRINGS)
@pytest.mark.parametrize("kind", _KINDS)
def test_injection_in_column_name_reaches_the_prompt_as_data_only(
    db_session: Any, admin: User, monkeypatch: pytest.MonkeyPatch, kind: str, injection: str
) -> None:
    suite = make_sql_suite(db_session, admin)
    monkeypatch.setattr(profile_service_module, "list_columns", lambda *_a, **_kw: [injection])
    monkeypatch.setattr(
        profile_service_module, "profile_connection", lambda *_a, **_kw: _empty_profile(injection)
    )
    invocation = _invocation(db_session, kind, suite, admin)
    if kind == llm_sqlgen.SQLGEN_KIND:
        invocation.request = {**(invocation.request or {}), "include_profile": True}
        db_session.commit()

    prompt = _build_prompt(kind, db_session, invocation, FakeSecretStore())
    assert injection in prompt


# ── top_value and range slots (checksuggest only — sql-gen's top_n=0 posture ──
# means neither ever reaches its prompt at all) ──────────────────────────────


@pytest.mark.parametrize("injection", PROMPT_INJECTION_STRINGS)
def test_injection_in_top_value_reaches_the_checksuggest_prompt_as_data_only(
    db_session: Any, admin: User, monkeypatch: pytest.MonkeyPatch, injection: str
) -> None:
    suite = make_sql_suite(db_session, admin)  # no column_policy: QTY is not sensitive
    monkeypatch.setattr(profile_service_module, "list_columns", lambda *_a, **_kw: ["QTY"])
    profile = ProfileResult(
        row_count=10,
        columns=[
            ColumnProfile(
                column="QTY",
                null_count=0,
                null_fraction=0.0,
                distinct_count=2,
                min_value=1,
                max_value=9,
                top_values=[{"value": injection, "count": 3}],
            )
        ],
    )
    monkeypatch.setattr(profile_service_module, "profile_connection", lambda *_a, **_kw: profile)
    invocation = _invocation(db_session, llm_checksuggest.CHECKSUGGEST_KIND, suite, admin)

    prompt = _build_prompt(
        llm_checksuggest.CHECKSUGGEST_KIND, db_session, invocation, FakeSecretStore()
    )
    # rendered via repr() — quoted/escape-visible, but must still carry the string.
    assert repr(injection) in prompt


@pytest.mark.parametrize("injection", PROMPT_INJECTION_STRINGS)
def test_injection_in_column_range_reaches_the_checksuggest_prompt_as_data_only(
    db_session: Any, admin: User, monkeypatch: pytest.MonkeyPatch, injection: str
) -> None:
    """min_value/max_value (a text column's lexical MIN/MAX) render via a bare
    f-string, unlike top_values — a distinct slot from the same warehouse trust
    class and worth its own proof.
    """
    suite = make_sql_suite(db_session, admin)
    monkeypatch.setattr(profile_service_module, "list_columns", lambda *_a, **_kw: ["CODE"])
    profile = ProfileResult(
        row_count=5,
        columns=[
            ColumnProfile(
                column="CODE",
                null_count=0,
                null_fraction=0.0,
                distinct_count=5,
                min_value=injection,
                max_value="z",
                top_values=[],
            )
        ],
    )
    monkeypatch.setattr(profile_service_module, "profile_connection", lambda *_a, **_kw: profile)
    invocation = _invocation(db_session, llm_checksuggest.CHECKSUGGEST_KIND, suite, admin)

    prompt = _build_prompt(
        llm_checksuggest.CHECKSUGGEST_KIND, db_session, invocation, FakeSecretStore()
    )
    assert injection in prompt


# ── table / schema name slot ─────────────────────────────────────────────────


@pytest.mark.parametrize("injection", PROMPT_INJECTION_STRINGS)
def test_injection_in_table_name_reaches_sqlgen_prompt_as_data_only(
    db_session: Any, admin: User, monkeypatch: pytest.MonkeyPatch, injection: str
) -> None:
    suite = make_sql_suite(db_session, admin, target={"table": injection, "schema": "RETAIL"})
    monkeypatch.setattr(profile_service_module, "list_columns", lambda *_a, **_kw: ["ID"])
    invocation = _invocation(db_session, llm_sqlgen.SQLGEN_KIND, suite, admin)

    prompt = _build_prompt(llm_sqlgen.SQLGEN_KIND, db_session, invocation, FakeSecretStore())
    # target fields are whitespace-stripped (`_clean()`) before assembly.
    assert injection.strip() in prompt


@pytest.mark.parametrize("injection", PROMPT_INJECTION_STRINGS)
def test_injection_in_schema_name_reaches_checksuggest_prompt_as_data_only(
    db_session: Any, admin: User, monkeypatch: pytest.MonkeyPatch, injection: str
) -> None:
    suite = make_sql_suite(db_session, admin, target={"table": "ORDERS", "schema": injection})
    monkeypatch.setattr(profile_service_module, "list_columns", lambda *_a, **_kw: ["ID"])
    monkeypatch.setattr(
        profile_service_module, "profile_connection", lambda *_a, **_kw: _empty_profile("ID")
    )
    invocation = _invocation(db_session, llm_checksuggest.CHECKSUGGEST_KIND, suite, admin)

    prompt = _build_prompt(
        llm_checksuggest.CHECKSUGGEST_KIND, db_session, invocation, FakeSecretStore()
    )
    assert injection.strip() in prompt


# ── PII discipline: structural, not incidental ───────────────────────────────


def test_neither_builder_touches_result_or_sample_failures() -> None:
    """No raw sample row can reach a prompt if `Result` is never even imported —
    a structural guarantee, not an incidental one (the #849 lesson: test the
    pipeline, not a scrub helper that could be bypassed).
    """
    import inspect

    for module in (llm_sqlgen, llm_checksuggest):
        assert "sample_failures" not in inspect.getsource(module)
        assert not hasattr(module, "Result")


# ── outbound: the response is untrusted too ──────────────────────────────────


class _StructuredProvider:
    model = "fake"

    def __init__(self, parsed: dict[str, Any]) -> None:
        self._parsed = parsed

    def complete(self, prompt: str, **_kw: Any) -> LLMResult:  # pragma: no cover - unused
        return LLMResult(text=str(self._parsed))

    def complete_structured(self, prompt: str, *, schema: dict[str, Any], **_kw: Any) -> LLMResult:
        return LLMResult(text="", parsed=self._parsed, input_tokens=3, output_tokens=1)


def _sqlgen_payload(marker: str) -> dict[str, Any]:
    return {"sql": f"SELECT '{marker}'", "explanation": marker}


def _checksuggest_payload(marker: str) -> dict[str, Any]:
    return {
        "suggestions": [
            {
                "expectation_type": "expect_column_values_to_not_be_null",
                "name": marker,
                "rationale": marker,
                "config": {"column": "EMAIL"},
            }
        ]
    }


@pytest.mark.parametrize(
    "kind, payload_for",
    [
        (llm_sqlgen.SQLGEN_KIND, _sqlgen_payload),
        (llm_checksuggest.CHECKSUGGEST_KIND, _checksuggest_payload),
    ],
)
def test_the_response_is_never_logged_unredacted(
    db_session: Any,
    admin: User,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    payload_for: Any,
) -> None:
    """The response is the untrusted OUTPUT of a system we do not control — it
    must never land in structured logs, which routinely reach a lower-trust
    sink than the DB row itself.
    """
    marker = f"INJECTION-MARKER-{uuid.uuid4().hex}"
    suite = make_sql_suite(db_session, admin)
    monkeypatch.setattr(profile_service_module, "list_columns", lambda *_a, **_kw: ["EMAIL"])
    monkeypatch.setattr(
        profile_service_module, "profile_connection", lambda *_a, **_kw: _empty_profile("EMAIL")
    )
    monkeypatch.setattr(
        llm_service, "build_provider", lambda *_a, **_kw: _StructuredProvider(payload_for(marker))
    )
    invocation = _invocation(db_session, kind, suite, admin)

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
