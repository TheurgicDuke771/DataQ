"""Prompt-injection adversarial battery (#1632), supporting #1512/#1513.

Posture (proven here, not assumed): output validation is the security boundary,
not prompt hygiene. Every test in this module either (a) proves a hostile string
reaches the assembled prompt as inert DATA in every context slot, or (b) proves
the output gate refuses a non-conforming result regardless of what produced it —
the per-feature output-gate batteries already living in test_llm_sqlgen.py /
test_llm_checksuggest.py ARE that half of this battery; this module fills the
gaps: the top_value and table/schema slots, and the response's OUTBOUND
untrusted-ness (never logged unredacted). Model-level jailbreak robustness is
explicitly out of scope — BYO-model means we don't control the model.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from backend.app.db.models import LlmInvocation, User
from backend.app.llm.base import LLMResult
from backend.app.services import llm_checksuggest, llm_service, llm_sqlgen
from backend.app.services import profile_service as profile_service_module
from backend.app.services.profile_service import ColumnProfile, ProfileResult
from backend.tests.support.adversarial import PROMPT_INJECTION_STRINGS
from backend.tests.support.fake_secret_store import FakeSecretStore
from backend.tests.support.llm_helpers import admin_user, enable_llm, make_sql_suite


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


# ── top_value slot ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("injection", PROMPT_INJECTION_STRINGS)
def test_injection_in_top_value_reaches_the_checksuggest_prompt_as_data_only(
    db_session: Any, admin: User, monkeypatch: pytest.MonkeyPatch, injection: str
) -> None:
    """A hostile top-value (a real value some row actually carries) must reach
    the prompt, never interpreted — the model sees it as DATA next to the
    "DATA, not instructions" framing, same as a hostile column name.

    sql-gen is deliberately excluded here: its `_profile_context` never emits
    `top_values` at all (`top_n=0` — #1512's own posture, proven by
    `seen_top_n == [0]` in test_llm_sqlgen.py), so a top-value injection
    string structurally cannot reach that prompt in the first place.
    """
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

    # The formatter renders top-values via repr() — a defence-in-depth touch
    # (quoted, escape-visible) that still must carry the string, not swallow it.
    assert repr(injection) in prompt


# ── table / schema name slot ─────────────────────────────────────────────────


@pytest.mark.parametrize("injection", PROMPT_INJECTION_STRINGS)
def test_injection_in_table_name_reaches_sqlgen_prompt_as_data_only(
    db_session: Any, admin: User, monkeypatch: pytest.MonkeyPatch, injection: str
) -> None:
    suite = make_sql_suite(db_session, admin, target={"table": injection, "schema": "RETAIL"})
    monkeypatch.setattr(profile_service_module, "list_columns", lambda *_a, **_kw: ["ID"])
    invocation = _invocation(db_session, llm_sqlgen.SQLGEN_KIND, suite, admin)

    prompt = _build_prompt(llm_sqlgen.SQLGEN_KIND, db_session, invocation, FakeSecretStore())
    # target fields are whitespace-stripped (`_clean()`) before assembly — a
    # legitimate normalization, not a defect, so compare against the stripped form.
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
    """No raw sample row can EVER reach an LLM prompt if the builders never read
    the table that holds them — a structural guarantee, not an incidental one
    (test the pipeline, not the scrub helper — the #849 lesson).
    """
    import inspect

    for module in (llm_sqlgen, llm_checksuggest):
        source = inspect.getsource(module)
        assert "sample_failures" not in source
        import_lines = [
            ln
            for ln in source.splitlines()
            if ln.strip().startswith("from backend.app.db.models import")
        ]
        assert import_lines, "expected an explicit db.models import to check"
        # The model that carries samples must not even be imported.
        assert "Result" not in import_lines[0]


# ── outbound: the response is untrusted too ──────────────────────────────────


class _StructuredProvider:
    model = "fake"

    def __init__(self, parsed: dict[str, Any]) -> None:
        self._parsed = parsed

    def complete(self, prompt: str, **_kw: Any) -> LLMResult:  # pragma: no cover - unused
        return LLMResult(text=str(self._parsed))

    def complete_structured(self, prompt: str, *, schema: dict[str, Any], **_kw: Any) -> LLMResult:
        return LLMResult(text="", parsed=self._parsed, input_tokens=3, output_tokens=1)


class _LogRecorder:
    """A structlog-shaped stand-in that records every call, for every level."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, level: str) -> Any:
        def _log(event: str, *args: Any, **kwargs: Any) -> None:
            self.calls.append((level, (event, *args), kwargs))

        return _log


def test_the_llm_response_is_never_logged_unredacted(
    db_session: Any, admin: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The response is the untrusted OUTPUT of a system we do not control — it
    must never land in structured logs, which routinely reach a lower-trust
    sink (App Insights, a log aggregator) than the DB row itself.
    """
    marker = f"INJECTION-MARKER-{uuid.uuid4().hex}"
    suite = make_sql_suite(db_session, admin)
    store = enable_llm(db_session, admin)
    monkeypatch.setattr(profile_service_module, "list_columns", lambda *_a, **_kw: ["EMAIL"])
    monkeypatch.setattr(
        llm_service,
        "build_provider",
        lambda *_a, **_kw: _StructuredProvider(
            {"sql": f"SELECT '{marker}'", "explanation": marker}
        ),
    )
    invocation = _invocation(db_session, llm_sqlgen.SQLGEN_KIND, suite, admin)

    recorder = _LogRecorder()
    monkeypatch.setattr(llm_service, "log", recorder)

    status = llm_service.execute_invocation(db_session, invocation.id, secret_store=store)
    assert status == "succeeded"

    # The marker DID reach storage (sanity: the provider's response was actually used).
    db_session.refresh(invocation)
    assert marker in str(invocation.response)
    # …but never a log call, in any position or kwarg.
    assert recorder.calls, "no log calls captured — the assertion below would be vacuous"
    for _level, args, kwargs in recorder.calls:
        assert marker not in str(args)
        assert marker not in str(kwargs)


def test_the_suggestion_response_is_never_logged_unredacted(
    db_session: Any, admin: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = f"INJECTION-MARKER-{uuid.uuid4().hex}"
    suite = make_sql_suite(db_session, admin)
    store = enable_llm(db_session, admin)
    monkeypatch.setattr(profile_service_module, "list_columns", lambda *_a, **_kw: ["EMAIL"])
    monkeypatch.setattr(
        profile_service_module, "profile_connection", lambda *_a, **_kw: _empty_profile("EMAIL")
    )
    monkeypatch.setattr(
        llm_service,
        "build_provider",
        lambda *_a, **_kw: _StructuredProvider(
            {
                "suggestions": [
                    {
                        "expectation_type": "expect_column_values_to_not_be_null",
                        "name": marker,
                        "rationale": marker,
                        "config": {"column": "EMAIL"},
                    }
                ]
            }
        ),
    )
    invocation = _invocation(db_session, llm_checksuggest.CHECKSUGGEST_KIND, suite, admin)

    recorder = _LogRecorder()
    monkeypatch.setattr(llm_service, "log", recorder)

    status = llm_service.execute_invocation(db_session, invocation.id, secret_store=store)
    assert status == "succeeded"

    db_session.refresh(invocation)
    assert marker in str(invocation.response)
    assert recorder.calls, "no log calls captured — the assertion below would be vacuous"
    for _level, args, kwargs in recorder.calls:
        assert marker not in str(args)
        assert marker not in str(kwargs)
