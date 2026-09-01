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
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.core.secrets import (
    SecretNotFoundError,
    SecretStore,
    SecretStoreUnavailableError,
)
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
    LLMOutputInvalidError,
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
    session: Session, *, draft: LlmSettingsDraft, secret_store: SecretStore, actor: User
) -> dict[str, Any]:
    """Live reachability probe of a DRAFT config (nothing persisted, except the
    `LlmInvocation` audit row below). A draft without a key falls back to the
    stored credential only when the destination matches the stored row — the
    same rule `save_settings` enforces.
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
            # A probe must report, not 500 — and an outage stays distinct from a
            # missing secret (the ADR 0039 rule). Neither path below reaches the
            # provider, so — unlike the round-trip further down — nothing is
            # recorded here: an invocation row would misrepresent a call that
            # never happened.
            try:
                api_key = secret_store.get(row.api_key_secret_ref)
            except SecretNotFoundError:
                return {
                    "ok": False,
                    "error_code": "llm_credential_missing",
                    "error": "the stored API key no longer exists — re-supply it",
                }
            except SecretStoreUnavailableError:
                return {
                    "ok": False,
                    "error_code": "secret_store_unavailable",
                    "error": "the secret store is unreachable — the stored key could not be read",
                }
    # #1773: this IS a genuine outbound round-trip (a fixed, non-data test
    # prompt, but a real call against the admin's chosen — possibly draft,
    # possibly not-yet-`enabled` — endpoint), so it gets the same audit row
    # every feature invocation does; the "every call is recorded" compliance
    # claim (`admin.py`'s `_llm_intelligence_transfer`) was false without it.
    # `kind="ping"` — reserved in `LLM_INVOCATION_KINDS` for exactly this,
    # never dispatched through `execute_invocation`/`KIND_BUILDERS` since this
    # call is synchronous, not queued.
    invocation = LlmInvocation(
        kind="ping", status="running", requested_by_user_id=actor.id, started_at=datetime.now(UTC)
    )
    session.add(invocation)
    session.flush()
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
        invocation.status = "failed"
        invocation.error = f"{exc.code}: {exc.message}"[:1024]
        invocation.finished_at = datetime.now(UTC)
        invocation.duration_ms = int((time.monotonic() - started) * 1000)
        session.commit()
        return {"ok": False, "error_code": exc.code, "error": exc.message}
    invocation.status = "succeeded"
    invocation.input_tokens = result.input_tokens
    invocation.output_tokens = result.output_tokens
    invocation.finished_at = datetime.now(UTC)
    invocation.duration_ms = int((time.monotonic() - started) * 1000)
    session.commit()
    return {
        "ok": True,
        "model": draft.model,
        "latency_ms": invocation.duration_ms,
        "reply_chars": len(result.text),
    }


# ── Invocation lifecycle ─────────────────────────────────────────────────────

#: kind → builder(session, invocation, secret_store) -> (prompt, system, schema|None).
#: Feature modules register in `services/llm_kinds.py` — the one import both the
#: worker and the API load, so a kind cannot be enqueueable but unexecutable.
KIND_BUILDERS: dict[
    str,
    Callable[[Session, LlmInvocation, SecretStore], tuple[str, str | None, dict[str, Any] | None]],
] = {}

#: kind → validator(session, invocation, parsed_payload) -> stored payload.
#: The per-kind OUTPUT gate (e.g. the ADR 0019 SQL validator); raises DataQError to fail the row.
KIND_VALIDATORS: dict[str, Callable[[Session, LlmInvocation, dict[str, Any]], dict[str, Any]]] = {}


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


def _scrub_nul(value: Any) -> Any:
    """Strip NUL from model output — Postgres rejects it in text AND JSONB, and
    the model is external input none of the request-side screens cover.
    """
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {_scrub_nul(k): _scrub_nul(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_nul(item) for item in value]
    return value


def execute_invocation(
    session: Session, invocation_id: uuid.UUID, *, secret_store: SecretStore
) -> str:
    """Worker-side body: build the kind's prompt, call the provider, persist the
    outcome. Always lands the row in a terminal state, including when the model's
    own output is unstorable — a row must never strand in `running`.
    """
    # Conditional claim: a duplicate delivery (broker redelivery, double
    # dispatch) finds no `pending` row and no-ops instead of paying twice.
    claim = session.execute(
        update(LlmInvocation)
        .where(LlmInvocation.id == invocation_id, LlmInvocation.status == "pending")
        .values(status="running", started_at=datetime.now(UTC))
    )
    claimed = getattr(claim, "rowcount", 0)
    session.commit()
    if claimed == 0:
        return "skipped"
    invocation = session.get(LlmInvocation, invocation_id)
    assert invocation is not None  # just claimed it  # nosec B101
    started = time.monotonic()
    # Accumulated locally, not written onto the tracked ORM object: the terminal
    # write below is an explicit status-guarded UPDATE (the reap-race fix, #1716
    # review), and a dirty ORM object would autoflush its own unguarded UPDATE
    # first and reintroduce exactly that race.
    terminal: dict[str, Any] = {}
    try:
        builder = KIND_BUILDERS.get(invocation.kind)
        if builder is None:
            # A wiring bug, not a provider fault — surfaces as `internal:`.
            raise RuntimeError(f"no builder registered for kind {invocation.kind!r}")
        prompt, system, schema = builder(session, invocation, secret_store)
        terminal["context_fingerprint"] = hashlib.sha256(
            (prompt + (system or "")).encode()
        ).hexdigest()
        provider = build_provider(session, secret_store)
        if schema is not None:
            result: LLMResult = provider.complete_structured(prompt, schema=schema, system=system)
            if result.parsed is None:
                raise LLMOutputInvalidError("provider returned no structured output")
            payload: dict[str, Any] = _scrub_nul(result.parsed)
        else:
            result = provider.complete(prompt, system=system)
            payload = {"text": _scrub_nul(result.text)}
        # Paid tokens are recorded whether or not the OUTPUT gate below refuses —
        # the row is the cost record, and a refused generation still billed.
        terminal["input_tokens"] = result.input_tokens
        terminal["output_tokens"] = result.output_tokens
        # The gate sees the exact bytes that will be stored (scrub BEFORE
        # validate): a NUL inside a keyword must not split tokens for the
        # validator and re-join in the stored copy.
        validator = KIND_VALIDATORS.get(invocation.kind)
        if validator is not None:
            payload = validator(session, invocation, payload)
        terminal["response"] = _scrub_nul(payload)
        terminal["status"] = "succeeded"
    except DataQError as exc:
        terminal["status"] = "failed"
        terminal["error"] = str(_scrub_nul(f"{exc.code}: {exc.message}"))[:1024]
    except Exception as exc:  # a driver-boundary surprise must still terminate the row
        terminal["status"] = "failed"
        terminal["error"] = f"internal: {exc.__class__.__name__}"[:1024]
        log.exception("llm_invocation_crashed", invocation_id=str(invocation_id))
    terminal["duration_ms"] = int((time.monotonic() - started) * 1000)
    terminal["finished_at"] = datetime.now(UTC)

    def _persist(values: dict[str, Any]) -> int:
        # Guarded like the claim above: a row the reaper already closed out
        # (#1644) must not be resurrected by a worker that was merely slow, not
        # dead.
        outcome = session.execute(
            update(LlmInvocation)
            .where(LlmInvocation.id == invocation_id, LlmInvocation.status == "running")
            .values(**values)
        )
        return getattr(outcome, "rowcount", 0)

    try:
        affected = _persist(terminal)
        session.commit()
    except Exception:
        # Unstorable payload (the scrub is belt, this is braces): drop the
        # payload, keep the terminal state — `running` forever is the one
        # unacceptable outcome.
        log.exception("llm_invocation_persist_failed", invocation_id=str(invocation_id))
        session.rollback()
        affected = _persist(
            {
                "status": "failed",
                "error": "internal: result could not be stored",
                "finished_at": datetime.now(UTC),
            }
        )
        session.commit()
        if affected == 0:
            log.warning(
                "llm_invocation_result_superseded",
                invocation_id=str(invocation_id),
                attempted_status=terminal.get("status"),
                input_tokens=terminal.get("input_tokens"),
                output_tokens=terminal.get("output_tokens"),
            )
            return "superseded"
        return "failed"
    if affected == 0:
        # Reaped out from under this worker while it was still working: the row
        # itself must not be resurrected, but a genuinely succeeded call still
        # billed — the tokens are recorded here since there is nowhere left to
        # persist them (the row the cost record lives on is already closed).
        log.warning(
            "llm_invocation_result_superseded",
            invocation_id=str(invocation_id),
            attempted_status=terminal["status"],
            input_tokens=terminal.get("input_tokens"),
            output_tokens=terminal.get("output_tokens"),
        )
        return "superseded"
    log.info(
        "llm_invocation_finished",
        invocation_id=str(invocation_id),
        kind=invocation.kind,
        status=terminal["status"],
        duration_ms=terminal["duration_ms"],
    )
    return str(terminal["status"])


def get_visible_invocation(
    session: Session, invocation_id: uuid.UUID, *, user: User, is_admin: bool
) -> LlmInvocation | None:
    invocation = session.get(LlmInvocation, invocation_id)
    if invocation is None:
        return None
    if not is_admin and invocation.requested_by_user_id != user.id:
        return None
    return invocation


_PENDING_REAP_REASON = (
    "reaped: stuck in pending past the threshold — the dispatch may have been lost"
    " before a worker claimed it, or the task is still queued behind other work in"
    " the shared worker queue (#1726/#1777: no dedicated queue exists yet)"
)
_RUNNING_REAP_REASON = (
    "reaped: stuck in running past the threshold — the worker may have been killed"
    " mid-call, or the provider/warehouse call is simply taking longer than usual"
    " (a cold warehouse resume or a wide-table profile can extend this, #1726)"
)


def reap_stuck_invocations(
    session: Session,
    *,
    pending_threshold_minutes: int,
    running_threshold_minutes: int,
    now: datetime | None = None,
) -> list[LlmInvocation]:
    """Fail `llm_invocations` stranded past their status's threshold (#1644).

    `execute_invocation`'s terminal-state guarantee only covers an in-process
    exception; a row can strand in `pending` (API died before `send_task`, or the
    broker dropped the message) or `running` (worker SIGKILL/OOM mid-provider-call).
    """
    # Conditional UPDATE (same idiom as execute_invocation's claim): the WHERE-status
    # guard is re-checked by Postgres at UPDATE time, so a row a worker legitimately
    # finished between our SELECT and our COMMIT can never be clobbered back to
    # `failed` — a plain select-then-mutate-then-commit would race it (#1716 review).
    moment = now or datetime.now(UTC)
    reaped_ids: list[uuid.UUID] = []
    if pending_threshold_minutes > 0:
        cutoff = moment - timedelta(minutes=pending_threshold_minutes)
        result = session.execute(
            update(LlmInvocation)
            .where(LlmInvocation.status == "pending", LlmInvocation.created_at < cutoff)
            .values(status="failed", error=_PENDING_REAP_REASON, finished_at=moment)
            .returning(LlmInvocation.id)
        )
        reaped_ids.extend(result.scalars().all())
    if running_threshold_minutes > 0:
        cutoff = moment - timedelta(minutes=running_threshold_minutes)
        result = session.execute(
            update(LlmInvocation)
            .where(LlmInvocation.status == "running", LlmInvocation.started_at < cutoff)
            .values(status="failed", error=_RUNNING_REAP_REASON, finished_at=moment)
            .returning(LlmInvocation.id)
        )
        reaped_ids.extend(result.scalars().all())
    if not reaped_ids:
        return []
    session.commit()
    log.warning(
        "llm_invocations_reaped",
        count=len(reaped_ids),
        pending_threshold_minutes=pending_threshold_minutes,
        running_threshold_minutes=running_threshold_minutes,
        invocation_ids=[str(i) for i in reaped_ids],
    )
    return list(session.scalars(select(LlmInvocation).where(LlmInvocation.id.in_(reaped_ids))))


def list_recent_invocations(session: Session, *, limit: int = 50) -> list[LlmInvocation]:
    return list(
        session.scalars(
            select(LlmInvocation).order_by(LlmInvocation.created_at.desc()).limit(limit)
        )
    )
