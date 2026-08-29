"""LLM provider config + invocation lifecycle (ADR 0042, #1511).

The config is a singleton row; the credential lives in the SecretStore under a
minted ref. The invocation table is both the UI's polling target and the G4
audit/cost record. Feature prompt-builders register in `KIND_BUILDERS` from
their own modules — the seam knows kinds only as strings.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.db.models import (
    LLM_PROVIDERS,
    LLM_STRUCTURED_OUTPUT_MODES,
    LlmInvocation,
    LlmSetting,
    User,
)
from backend.app.llm.anthropic_provider import AnthropicProvider
from backend.app.llm.base import (
    LLMNotConfiguredError,
    LLMProvider,
    LLMProviderError,
    LLMResult,
    LLMUnavailableError,
)
from backend.app.llm.openai_compat import OpenAICompatProvider
from backend.app.services import audit_service

log = get_logger(__name__)

_SECRET_REF_PREFIX = "llm-provider"  # noqa: S105 # nosec B105 — ref prefix, not a secret
_TEST_TIMEOUT_SECONDS = 20.0
_SETTINGS_ROW_ID = 1


class LLMConfigInvalidError(DataQError):
    status_code = 422
    code = "llm_config_invalid"


@dataclass(frozen=True)
class LlmSettingsDraft:
    provider: str
    model: str
    base_url: str | None = None
    api_key: str | None = None
    structured_output: str = "native"
    enabled: bool = True


def get_settings_row(session: Session) -> LlmSetting | None:
    return session.get(LlmSetting, _SETTINGS_ROW_ID)


def _validate_draft(draft: LlmSettingsDraft) -> None:
    problems: list[str] = []
    if draft.provider not in LLM_PROVIDERS:
        problems.append(f"provider must be one of {LLM_PROVIDERS}")
    if draft.structured_output not in LLM_STRUCTURED_OUTPUT_MODES:
        problems.append(f"structured_output must be one of {LLM_STRUCTURED_OUTPUT_MODES}")
    if not draft.model.strip():
        problems.append("model is required")
    if draft.provider == "openai_compatible":
        if not draft.base_url:
            problems.append("base_url is required for openai_compatible")
        elif not draft.base_url.startswith(("http://", "https://")):
            problems.append("base_url must be an http(s) URL")
    if draft.provider == "anthropic":
        if draft.base_url and not draft.base_url.startswith("https://"):
            problems.append("anthropic base_url must be https")
    if draft.api_key == "":
        problems.append("api_key must be non-empty when supplied")
    if problems:
        raise LLMConfigInvalidError("; ".join(problems))


def save_settings(
    session: Session,
    *,
    draft: LlmSettingsDraft,
    actor: User,
    secret_store: SecretStore,
) -> LlmSetting:
    """Upsert the singleton config. The destination-field rule: a change to
    `provider` or `base_url` (where the credential is SENT) is refused unless
    the credential is re-supplied — redirecting a stored secret requires
    already holding it.
    """
    _validate_draft(draft)
    row = get_settings_row(session)
    before = audit_service.snapshot("llm_setting", row) if row is not None else None
    if row is not None and draft.api_key is None:
        destination_moved = (draft.provider, draft.base_url) != (row.provider, row.base_url)
        if destination_moved and row.api_key_secret_ref is not None:
            raise LLMConfigInvalidError(
                "changing provider or base_url requires re-supplying the API key"
            )
    if (
        draft.provider == "anthropic"
        and draft.api_key is None
        and (row is None or row.api_key_secret_ref is None)
    ):
        raise LLMConfigInvalidError("anthropic requires an api_key")

    if row is None:
        row = LlmSetting(id=_SETTINGS_ROW_ID, provider=draft.provider, model=draft.model)
        session.add(row)
    row.provider = draft.provider
    row.base_url = draft.base_url
    row.model = draft.model
    row.structured_output = draft.structured_output
    row.enabled = draft.enabled
    if draft.api_key is not None:
        ref = row.api_key_secret_ref or f"{_SECRET_REF_PREFIX}-{uuid.uuid4().hex[:12]}"
        secret_store.set(ref, draft.api_key)
        row.api_key_secret_ref = ref
    session.flush()
    # `record` (not `record_entity_change`): the singleton's integer id can't ride
    # the UUID `entity_id` column — this is ADR 0041's "no single row" NULL case.
    audit_service.record(
        session,
        action="llm_setting.update",
        entity_type="llm_setting",
        entity_id=None,
        actor=actor,
        before=before,
        after=audit_service.snapshot("llm_setting", row),
    )
    log.info("llm_settings_saved", provider=row.provider, enabled=row.enabled)
    return row


def build_provider(
    session: Session, secret_store: SecretStore, *, require_enabled: bool = True
) -> LLMProvider:
    row = get_settings_row(session)
    if row is None or (require_enabled and not row.enabled):
        raise LLMNotConfiguredError("no LLM provider is configured — a workspace admin can add one")
    return _provider_from(
        provider=row.provider,
        base_url=row.base_url,
        model=row.model,
        structured_output=row.structured_output,
        api_key=(
            secret_store.get(row.api_key_secret_ref) if row.api_key_secret_ref is not None else None
        ),
    )


def _provider_from(
    *,
    provider: str,
    base_url: str | None,
    model: str,
    structured_output: str,
    api_key: str | None,
) -> LLMProvider:
    if provider == "anthropic":
        if api_key is None:
            raise LLMConfigInvalidError("anthropic requires an api_key")
        return AnthropicProvider(model=model, api_key=api_key, base_url=base_url)
    if base_url is None:
        raise LLMConfigInvalidError("base_url is required for openai_compatible")
    return OpenAICompatProvider(
        base_url=base_url, model=model, api_key=api_key, structured_output=structured_output
    )


def test_settings(
    session: Session, *, draft: LlmSettingsDraft, secret_store: SecretStore
) -> dict[str, Any]:
    """Live reachability probe of a DRAFT config (nothing persisted). A draft
    without a key falls back to the stored credential only when the destination
    matches the stored row — the same rule `save_settings` enforces.
    """
    _validate_draft(draft)
    api_key = draft.api_key
    if api_key is None:
        row = get_settings_row(session)
        if (
            row is not None
            and row.api_key_secret_ref is not None
            and (draft.provider, draft.base_url) == (row.provider, row.base_url)
        ):
            api_key = secret_store.get(row.api_key_secret_ref)
    started = time.monotonic()
    try:
        provider = _provider_from(
            provider=draft.provider,
            base_url=draft.base_url,
            model=draft.model,
            structured_output=draft.structured_output,
            api_key=api_key,
        )
        result = provider.complete(
            "Reply with the single word: ok", max_tokens=16, timeout=_TEST_TIMEOUT_SECONDS
        )
    except (LLMUnavailableError, LLMProviderError, LLMConfigInvalidError) as exc:
        return {"ok": False, "error_code": exc.code, "error": exc.message}
    return {
        "ok": True,
        "model": draft.model,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "reply_chars": len(result.text),
    }


# ── Invocation lifecycle ─────────────────────────────────────────────────────

#: kind → builder(session, invocation) -> (prompt, system, schema|None).
#: Feature modules (SQL-gen, suggestions, RCA) register here at import time.
KIND_BUILDERS: dict[
    str, Callable[[Session, LlmInvocation], tuple[str, str | None, dict[str, Any] | None]]
] = {}


def create_invocation(
    session: Session,
    *,
    kind: str,
    requested_by: User,
    suite_id: uuid.UUID | None = None,
    request: dict[str, Any] | None = None,
) -> LlmInvocation:
    row = get_settings_row(session)
    if row is None or not row.enabled:
        raise LLMNotConfiguredError("no LLM provider is configured — a workspace admin can add one")
    invocation = LlmInvocation(
        kind=kind, requested_by_user_id=requested_by.id, suite_id=suite_id, request=request
    )
    session.add(invocation)
    session.flush()
    return invocation


def execute_invocation(
    session: Session, invocation_id: uuid.UUID, *, secret_store: SecretStore
) -> str:
    """Worker-side body: build the kind's prompt, call the provider, persist the
    outcome. Always lands the row in a terminal state — a crash mid-call must
    not leave `running` forever (the caller commits either way).
    """
    invocation = session.get(LlmInvocation, invocation_id)
    if invocation is None or invocation.status not in ("pending", "running"):
        return "skipped"
    invocation.status = "running"
    invocation.started_at = datetime.now(UTC)
    session.commit()
    started = time.monotonic()
    try:
        builder = KIND_BUILDERS.get(invocation.kind)
        if builder is None:
            raise LLMProviderError(f"no builder registered for kind {invocation.kind!r}")
        prompt, system, schema = builder(session, invocation)
        invocation.context_fingerprint = hashlib.sha256(
            (prompt + (system or "")).encode()
        ).hexdigest()
        provider = build_provider(session, secret_store)
        if schema is not None:
            result: LLMResult = provider.complete_structured(prompt, schema=schema, system=system)
            invocation.response = result.parsed
        else:
            result = provider.complete(prompt, system=system)
            invocation.response = {"text": result.text}
        invocation.status = "succeeded"
        invocation.input_tokens = result.input_tokens
        invocation.output_tokens = result.output_tokens
    except DataQError as exc:
        invocation.status = "failed"
        invocation.error = f"{exc.code}: {exc.message}"[:1024]
    except Exception as exc:  # a driver-boundary surprise must still terminate the row
        invocation.status = "failed"
        invocation.error = f"internal: {exc.__class__.__name__}"[:1024]
        log.exception("llm_invocation_crashed", invocation_id=str(invocation_id))
    invocation.duration_ms = int((time.monotonic() - started) * 1000)
    invocation.finished_at = datetime.now(UTC)
    session.commit()
    log.info(
        "llm_invocation_finished",
        invocation_id=str(invocation_id),
        kind=invocation.kind,
        status=invocation.status,
        duration_ms=invocation.duration_ms,
    )
    return invocation.status


def get_visible_invocation(
    session: Session, invocation_id: uuid.UUID, *, user: User, is_admin: bool
) -> LlmInvocation | None:
    invocation = session.get(LlmInvocation, invocation_id)
    if invocation is None:
        return None
    if not is_admin and invocation.requested_by_user_id != user.id:
        return None
    return invocation


def list_recent_invocations(session: Session, *, limit: int = 50) -> list[LlmInvocation]:
    return list(
        session.scalars(
            select(LlmInvocation).order_by(LlmInvocation.created_at.desc()).limit(limit)
        )
    )
