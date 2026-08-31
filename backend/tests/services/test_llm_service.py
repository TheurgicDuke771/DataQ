"""LLM settings + invocation lifecycle (ADR 0042)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import update

from backend.app.db.models import LlmInvocation, User
from backend.app.llm.base import LLMNotConfiguredError, LLMResult, LLMUnavailableError
from backend.app.services import llm_service
from backend.tests.support.fake_secret_store import FakeSecretStore


@pytest.fixture
def admin(db_session: Any) -> User:
    user = User(
        id=uuid.uuid4(),
        aad_object_id=None,
        email=f"llm-admin-{uuid.uuid4().hex[:8]}@example.com",
        role="admin",
    )
    db_session.add(user)
    db_session.commit()
    return user


def _draft(**overrides: Any) -> llm_service.LlmSettingsDraft:
    defaults: dict[str, Any] = {
        "provider": "openai_compatible",
        "model": "qwen2.5:3b",
        "base_url": "http://ollama.local/v1",
        "api_key": None,
        "structured_output": "prompt_json",
        "enabled": True,
    }
    defaults.update(overrides)
    return llm_service.LlmSettingsDraft(**defaults)


class _FakeProvider:
    model = "fake"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []

    def complete(self, prompt: str, **_kw: Any) -> LLMResult:
        self.calls.append(prompt)
        if self.fail:
            raise LLMUnavailableError("down")
        return LLMResult(text="ok", input_tokens=3, output_tokens=1)

    def complete_structured(self, prompt: str, *, schema: dict[str, Any], **_kw: Any) -> LLMResult:
        self.calls.append(prompt)
        if self.fail:
            raise LLMUnavailableError("down")
        return LLMResult(text="", parsed={"sql": "SELECT 1"}, input_tokens=3, output_tokens=1)


# ── settings ─────────────────────────────────────────────────────────────────


def test_save_mints_secret_ref_and_never_stores_the_key(db_session: Any, admin: User) -> None:
    store = FakeSecretStore()
    row = llm_service.save_settings(
        db_session, draft=_draft(api_key="sk-live-1"), actor=admin, secret_store=store
    )
    assert row.api_key_secret_ref is not None
    assert row.api_key_secret_ref.startswith("llm-provider-")
    assert store.data[row.api_key_secret_ref] == "sk-live-1"
    row_columns = {k: v for k, v in row.__dict__.items() if not k.startswith("_")}
    assert "sk-live-1" not in str(row_columns)


def test_save_audits_the_change(db_session: Any, admin: User) -> None:
    from backend.app.db.models import AuditEvent

    llm_service.save_settings(
        db_session, draft=_draft(api_key="sk-1"), actor=admin, secret_store=FakeSecretStore()
    )
    db_session.commit()
    event = db_session.query(AuditEvent).filter(AuditEvent.action == "llm_setting.update").one()
    assert event.after["provider"] == "openai_compatible"
    assert "sk-1" not in str(event.after)


def test_destination_move_without_key_is_refused(db_session: Any, admin: User) -> None:
    store = FakeSecretStore()
    llm_service.save_settings(
        db_session, draft=_draft(api_key="sk-1"), actor=admin, secret_store=store
    )
    with pytest.raises(llm_service.LLMConfigInvalidError):
        llm_service.save_settings(
            db_session,
            draft=_draft(base_url="http://evil.example/v1"),
            actor=admin,
            secret_store=store,
        )
    with pytest.raises(llm_service.LLMConfigInvalidError):
        llm_service.save_settings(
            db_session,
            draft=_draft(provider="anthropic", base_url=None),
            actor=admin,
            secret_store=store,
        )
    # Same destination, no key → allowed (e.g. toggling enabled or model).
    row = llm_service.save_settings(
        db_session, draft=_draft(model="other-model"), actor=admin, secret_store=store
    )
    assert row.model == "other-model"
    # Re-supplying the key WITH the move → allowed, same ref overwritten.
    row = llm_service.save_settings(
        db_session,
        draft=_draft(base_url="http://new.example/v1", api_key="sk-2"),
        actor=admin,
        secret_store=store,
    )
    assert row.api_key_secret_ref is not None
    assert store.data[row.api_key_secret_ref] == "sk-2"


def test_empty_api_key_is_refused(db_session: Any, admin: User) -> None:
    with pytest.raises(llm_service.LLMConfigInvalidError):
        llm_service.save_settings(
            db_session, draft=_draft(api_key=""), actor=admin, secret_store=FakeSecretStore()
        )


def test_anthropic_requires_key_and_openai_compat_requires_base_url(
    db_session: Any, admin: User
) -> None:
    with pytest.raises(llm_service.LLMConfigInvalidError):
        llm_service.save_settings(
            db_session,
            draft=_draft(provider="anthropic", base_url=None, api_key=None),
            actor=admin,
            secret_store=FakeSecretStore(),
        )
    with pytest.raises(llm_service.LLMConfigInvalidError):
        llm_service._validate_draft(_draft(base_url=None))


def test_build_provider_unconfigured_and_disabled_raise_not_configured(
    db_session: Any, admin: User
) -> None:
    with pytest.raises(LLMNotConfiguredError):
        llm_service.build_provider(db_session, FakeSecretStore())
    llm_service.save_settings(
        db_session,
        draft=_draft(api_key="sk-1", enabled=False),
        actor=admin,
        secret_store=FakeSecretStore({"x": "y"}),
    )
    with pytest.raises(LLMNotConfiguredError):
        llm_service.build_provider(db_session, FakeSecretStore())


def test_test_settings_uses_stored_key_only_for_same_destination(
    db_session: Any, admin: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FakeSecretStore()
    llm_service.save_settings(
        db_session, draft=_draft(api_key="sk-1"), actor=admin, secret_store=store
    )
    seen_keys: list[str | None] = []

    def _fake_provider_from(**kwargs: Any) -> _FakeProvider:
        seen_keys.append(kwargs["api_key"])
        return _FakeProvider()

    monkeypatch.setattr(llm_service, "_provider_from", _fake_provider_from)
    same = llm_service.test_settings(db_session, draft=_draft(), secret_store=store)
    assert same["ok"] is True
    moved = llm_service.test_settings(
        db_session, draft=_draft(base_url="http://other.example/v1"), secret_store=store
    )
    assert moved["ok"] is True
    assert seen_keys == ["sk-1", None]  # the stored key never follows a moved destination


def test_test_settings_reports_outage_as_outage(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm_service, "_provider_from", lambda **_kw: _FakeProvider(fail=True))
    out = llm_service.test_settings(db_session, draft=_draft(), secret_store=FakeSecretStore())
    assert out["ok"] is False
    assert out["error_code"] == "llm_provider_unavailable"


# ── invocation lifecycle ─────────────────────────────────────────────────────


def _enable(db_session: Any, admin: User, store: FakeSecretStore) -> None:
    llm_service.save_settings(
        db_session, draft=_draft(api_key="sk-1"), actor=admin, secret_store=store
    )


def test_create_invocation_requires_configured_enabled_provider(
    db_session: Any, admin: User
) -> None:
    with pytest.raises(LLMNotConfiguredError):
        llm_service.create_invocation(db_session, kind="ping", requested_by=admin)


def test_execute_invocation_success_path(
    db_session: Any, admin: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FakeSecretStore()
    _enable(db_session, admin, store)
    invocation = llm_service.create_invocation(db_session, kind="ping", requested_by=admin)
    db_session.commit()
    provider = _FakeProvider()
    monkeypatch.setattr(llm_service, "build_provider", lambda *_a, **_kw: provider)
    monkeypatch.setitem(
        llm_service.KIND_BUILDERS, "ping", lambda _s, _inv, _st: ("say ok", None, None)
    )
    status = llm_service.execute_invocation(db_session, invocation.id, secret_store=store)
    assert status == "succeeded"
    db_session.refresh(invocation)
    assert invocation.response == {"text": "ok"}
    assert invocation.context_fingerprint is not None
    assert invocation.duration_ms is not None
    assert invocation.finished_at is not None
    assert (invocation.input_tokens, invocation.output_tokens) == (3, 1)


def test_execute_invocation_structured_kind(
    db_session: Any, admin: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FakeSecretStore()
    _enable(db_session, admin, store)
    invocation = llm_service.create_invocation(
        db_session, kind="check_suggestion", requested_by=admin
    )
    db_session.commit()
    monkeypatch.setattr(llm_service, "build_provider", lambda *_a, **_kw: _FakeProvider())
    monkeypatch.setitem(
        llm_service.KIND_BUILDERS,
        "check_suggestion",
        lambda _s, _inv, _st: ("gen", "sys", {"type": "object"}),
    )
    # Pin the no-validator branch: if check_suggestion later registers a real
    # validator, this generic-lifecycle test must not silently start using it.
    monkeypatch.delitem(llm_service.KIND_VALIDATORS, "check_suggestion", raising=False)
    assert llm_service.execute_invocation(db_session, invocation.id, secret_store=store) == (
        "succeeded"
    )
    db_session.refresh(invocation)
    assert invocation.response == {"sql": "SELECT 1"}


def test_execute_invocation_outage_lands_failed_with_error_code(
    db_session: Any, admin: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FakeSecretStore()
    _enable(db_session, admin, store)
    invocation = llm_service.create_invocation(db_session, kind="ping", requested_by=admin)
    db_session.commit()
    monkeypatch.setattr(llm_service, "build_provider", lambda *_a, **_kw: _FakeProvider(fail=True))
    monkeypatch.setitem(
        llm_service.KIND_BUILDERS, "ping", lambda _s, _inv, _st: ("say ok", None, None)
    )
    assert llm_service.execute_invocation(db_session, invocation.id, secret_store=store) == "failed"
    db_session.refresh(invocation)
    assert invocation.error is not None
    assert invocation.error.startswith("llm_provider_unavailable:")


def test_execute_invocation_unregistered_kind_fails_terminal(db_session: Any, admin: User) -> None:
    """`ping` is reserved DB vocabulary with no production builder — the other
    tests in this file that need one register it temporarily via
    `monkeypatch.setitem`; here it stays unregistered on purpose (#1633: all
    THREE feature kinds now have builders, so this is the one kind left).
    """
    store = FakeSecretStore()
    _enable(db_session, admin, store)
    invocation = llm_service.create_invocation(db_session, kind="ping", requested_by=admin)
    db_session.commit()
    assert llm_service.execute_invocation(db_session, invocation.id, secret_store=store) == "failed"
    db_session.refresh(invocation)
    assert invocation.error is not None
    # A wiring bug is `internal:` — never a provider fault (the class name only;
    # the log line carries the detail).
    assert invocation.error == "internal: RuntimeError"


def test_execute_invocation_skips_terminal_rows(db_session: Any, admin: User) -> None:
    store = FakeSecretStore()
    _enable(db_session, admin, store)
    invocation = llm_service.create_invocation(db_session, kind="ping", requested_by=admin)
    invocation.status = "succeeded"
    db_session.commit()
    assert (
        llm_service.execute_invocation(db_session, invocation.id, secret_store=store) == "skipped"
    )
    assert llm_service.execute_invocation(db_session, uuid.uuid4(), secret_store=store) == "skipped"


def test_visibility_requester_or_admin_only(db_session: Any, admin: User) -> None:
    store = FakeSecretStore()
    _enable(db_session, admin, store)
    other = User(id=uuid.uuid4(), aad_object_id=None, email="other@example.com", role="member")
    db_session.add(other)
    invocation = llm_service.create_invocation(db_session, kind="ping", requested_by=admin)
    db_session.commit()
    assert (
        llm_service.get_visible_invocation(db_session, invocation.id, user=admin, is_admin=False)
        is not None
    )
    assert (
        llm_service.get_visible_invocation(db_session, invocation.id, user=other, is_admin=False)
        is None
    )
    assert (
        llm_service.get_visible_invocation(db_session, invocation.id, user=other, is_admin=True)
        is not None
    )


def test_invocation_kind_and_status_check_constraints(db_session: Any, admin: User) -> None:
    from sqlalchemy.exc import IntegrityError

    db_session.add(LlmInvocation(kind="not-a-kind", requested_by_user_id=admin.id))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_duplicate_delivery_is_a_noop(
    db_session: Any, admin: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FakeSecretStore()
    _enable(db_session, admin, store)
    invocation = llm_service.create_invocation(db_session, kind="ping", requested_by=admin)
    db_session.commit()
    provider = _FakeProvider()
    monkeypatch.setattr(llm_service, "build_provider", lambda *_a, **_kw: provider)
    monkeypatch.setitem(
        llm_service.KIND_BUILDERS, "ping", lambda _s, _inv, _st: ("say ok", None, None)
    )
    assert llm_service.execute_invocation(db_session, invocation.id, secret_store=store) == (
        "succeeded"
    )
    # Redelivery of the same task id: the pending→running claim finds nothing.
    assert llm_service.execute_invocation(db_session, invocation.id, secret_store=store) == (
        "skipped"
    )
    assert len(provider.calls) == 1  # paid exactly once

    stuck = llm_service.create_invocation(db_session, kind="ping", requested_by=admin)
    stuck.status = "running"
    db_session.commit()
    assert llm_service.execute_invocation(db_session, stuck.id, secret_store=store) == "skipped"


def test_nul_in_model_output_is_scrubbed_and_lands_terminal(
    db_session: Any, admin: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FakeSecretStore()
    _enable(db_session, admin, store)
    invocation = llm_service.create_invocation(db_session, kind="ping", requested_by=admin)
    db_session.commit()

    class _NulProvider(_FakeProvider):
        def complete(self, prompt: str, **_kw: Any) -> LLMResult:
            return LLMResult(text="ok\x00bad", input_tokens=1, output_tokens=1)

    monkeypatch.setattr(llm_service, "build_provider", lambda *_a, **_kw: _NulProvider())
    monkeypatch.setitem(llm_service.KIND_BUILDERS, "ping", lambda _s, _inv, _st: ("p", None, None))
    assert llm_service.execute_invocation(db_session, invocation.id, secret_store=store) == (
        "succeeded"
    )
    db_session.refresh(invocation)
    assert invocation.response == {"text": "okbad"}


def test_unstorable_persist_still_lands_failed_not_running(
    db_session: Any, admin: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FakeSecretStore()
    _enable(db_session, admin, store)
    invocation = llm_service.create_invocation(db_session, kind="ping", requested_by=admin)
    db_session.commit()
    monkeypatch.setattr(llm_service, "build_provider", lambda *_a, **_kw: _FakeProvider())
    monkeypatch.setitem(llm_service.KIND_BUILDERS, "ping", lambda _s, _inv, _st: ("p", None, None))
    # Defeat the scrub to prove the braces hold when the belt fails.
    monkeypatch.setattr(llm_service, "_scrub_nul", lambda v: v)

    class _RawNulProvider(_FakeProvider):
        def complete(self, prompt: str, **_kw: Any) -> LLMResult:
            return LLMResult(text="bad\x00", input_tokens=1, output_tokens=1)

    monkeypatch.setattr(llm_service, "build_provider", lambda *_a, **_kw: _RawNulProvider())
    assert llm_service.execute_invocation(db_session, invocation.id, secret_store=store) == "failed"
    db_session.expire_all()
    row = db_session.get(LlmInvocation, invocation.id)
    assert row.status == "failed"
    assert row.error == "internal: result could not be stored"


def test_result_does_not_resurrect_a_row_the_reaper_already_closed_out(
    db_session: Any, admin: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1716 review: a worker that is merely slow (not dead) must not overwrite a
    row the #1644 reaper already failed out from under it.
    """
    store = FakeSecretStore()
    _enable(db_session, admin, store)
    invocation = llm_service.create_invocation(db_session, kind="ping", requested_by=admin)
    db_session.commit()
    monkeypatch.setattr(llm_service, "build_provider", lambda *_a, **_kw: _FakeProvider())

    reap_reason = "reaped: the worker never finished — likely killed mid-call"

    def _builder_that_races_the_reaper(_s: Any, inv: Any, _st: Any) -> tuple[str, None, None]:
        # Stand-in for the #1644 beat reaper closing this row out mid-flight,
        # strictly between this worker's claim and its own terminal write.
        db_session.execute(
            update(LlmInvocation)
            .where(LlmInvocation.id == inv.id)
            .values(status="failed", error=reap_reason)
        )
        db_session.commit()
        return "say ok", None, None

    monkeypatch.setitem(llm_service.KIND_BUILDERS, "ping", _builder_that_races_the_reaper)

    assert (
        llm_service.execute_invocation(db_session, invocation.id, secret_store=store)
        == "superseded"
    )
    db_session.refresh(invocation)
    assert invocation.status == "failed"
    assert invocation.error == reap_reason
    assert invocation.response is None  # the worker's own (wasted) result never landed


def test_test_settings_reports_secret_store_states_distinctly(db_session: Any, admin: User) -> None:
    from backend.app.core.secrets import SecretStoreUnavailableError

    store = FakeSecretStore()
    llm_service.save_settings(
        db_session, draft=_draft(api_key="sk-1"), actor=admin, secret_store=store
    )
    store.data.clear()  # the ref now dangles
    missing = llm_service.test_settings(db_session, draft=_draft(), secret_store=store)
    assert missing == {
        "ok": False,
        "error_code": "llm_credential_missing",
        "error": "the stored API key no longer exists — re-supply it",
    }
    sealed = llm_service.test_settings(
        db_session,
        draft=_draft(),
        secret_store=FakeSecretStore(raise_on_get=SecretStoreUnavailableError("sealed")),
    )
    assert sealed["error_code"] == "secret_store_unavailable"


def test_suite_delete_keeps_the_invocation_record(db_session: Any, admin: User) -> None:
    import uuid as _uuid

    from backend.app.db.models import Connection, Suite

    store = FakeSecretStore()
    _enable(db_session, admin, store)
    connection = Connection(
        id=_uuid.uuid4(),
        name=f"c-{_uuid.uuid4().hex[:6]}",
        type="snowflake",
        env="dev",
        config={},
        created_by=admin.id,
    )
    db_session.add(connection)
    db_session.flush()
    suite = Suite(
        id=_uuid.uuid4(),
        name=f"s-{_uuid.uuid4().hex[:6]}",
        connection_id=connection.id,
        created_by=admin.id,
    )
    db_session.add(suite)
    db_session.flush()
    invocation = llm_service.create_invocation(
        db_session, kind="ping", requested_by=admin, suite_id=suite.id
    )
    db_session.commit()
    db_session.delete(suite)
    db_session.commit()
    db_session.expire_all()
    row = db_session.get(LlmInvocation, invocation.id)
    assert row is not None  # the cost/audit record outlives the suite
    assert row.suite_id is None
