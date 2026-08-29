"""Workspace-admin endpoints — the all-suites / all-users / access overview the
Admin page consumes, plus the SMTP pre-flight test (#737).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import ConfigDict
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel, ApiRequestModel
from backend.app.core.auth import require_workspace_admin
from backend.app.core.config import get_settings
from backend.app.core.secrets import SecretStore, get_secret_store
from backend.app.db.models import SuiteNotification, User
from backend.app.db.session import get_db
from backend.app.mcp.auth import mcp_enabled
from backend.app.services import admin_service as svc
from backend.app.services import (
    audit_chain,
    audit_read_service,
    audit_service,
    data_subject_requests,
    llm_service,
)
from backend.app.services.otp_mailer import OtpMailer

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_workspace_admin)],
)


class AdminSuiteRead(ApiModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    connection_name: str
    connection_type: str
    env: str
    #: `None` once the creating user is erased (#1319).
    owner_id: UUID | None
    owner_email: str | None
    owner_name: str | None
    check_count: int
    share_count: int
    created_at: datetime
    updated_at: datetime


class AdminUserRead(ApiModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    display_name: str | None
    last_seen_at: datetime | None
    created_at: datetime
    owned_suite_count: int
    shared_suite_count: int
    #: The STORED role — what the editor below writes.
    role: str
    #: Whether WORKSPACE_ADMIN_EMAILS grants this user admin regardless of the stored role, so the
    #: UI can explain a `member` row that is nonetheless an admin.
    allowlist_admin: bool


class AdminAccessRead(ApiModel):
    model_config = ConfigDict(from_attributes=True)

    suite_id: UUID
    suite_name: str
    user_id: UUID
    user_email: str
    user_name: str | None
    permission: str


@router.get("/suites", response_model=list[AdminSuiteRead], summary="All suites (admin)")
def all_suites(db: Annotated[Session, Depends(get_db)]) -> list[svc.AdminSuiteRow]:
    return svc.list_all_suites(db)


@router.get("/users", response_model=list[AdminUserRead], summary="All users (admin)")
def all_users(db: Annotated[Session, Depends(get_db)]) -> list[svc.AdminUserRow]:
    return svc.list_all_users(db)


@router.get("/access", response_model=list[AdminAccessRead], summary="Access overview (admin)")
def all_access(db: Annotated[Session, Depends(get_db)]) -> list[svc.AdminAccessRow]:
    return svc.list_all_access(db)


class UserRoleUpdate(ApiRequestModel):
    """`PATCH /admin/users/{id}/role` body (ADR 0033, #742)."""

    #: A Literal, not a bare `str`: an unknown tier is a 422 from the framework rather than
    #: something the service has to reject.
    role: Literal["admin", "member", "viewer"]


@router.patch(
    "/users/{user_id}/role",
    response_model=AdminUserRead,
    summary="Change a user's workspace role (admin)",
)
def set_user_role(
    user_id: UUID,
    payload: UserRoleUpdate,
    current_user: Annotated[User, Depends(require_workspace_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> AdminUserRead:
    """Set `user_id`'s stored workspace role — the one sanctioned way to demote."""
    svc.set_user_role(db, user_id, new_role=payload.role, actor=current_user)
    # Re-read through the SAME row builder the list uses, so the response carries the identical
    # computed fields (`allowlist_admin`, the suite counts).
    return AdminUserRead.model_validate(svc.get_admin_user(db, user_id))


class AdminWebhookRead(ApiModel):
    model_config = ConfigDict(from_attributes=True)

    provider: str
    auth: str
    inbound_url: str
    token_configured: bool
    signing_secret_name: str | None
    connection_names: list[str]


@router.get(
    "/orchestration/webhooks",
    response_model=list[AdminWebhookRead],
    summary="Inbound orchestration webhook config (admin)",
)
def orchestration_webhooks(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
) -> list[svc.WebhookConfigRow]:
    # The ADF row embeds the shared secret in the URL — admin-gated (router dep)
    # and never logged. Base URL: the configured public host, else the request's.
    base_url = get_settings().public_base_url or str(request.base_url)
    return svc.webhook_configs(db, base_url=base_url, secret_store=secret_store)


class AuthEmailTestResponse(ApiModel):
    status: str = "ok"
    to: str


@router.post(
    "/auth-email/test",
    response_model=AuthEmailTestResponse,
    summary="SMTP pre-flight test — send a test email to the caller (ADR 0032, #737)",
)
def test_auth_email(
    current_user: Annotated[User, Depends(require_workspace_admin)],
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
) -> AuthEmailTestResponse:
    """Send a real test message to the CALLER's own address over the configured
    `AUTH_EMAIL_*` transport, so a misconfigured mailer is caught at install time
    rather than at a teammate's first sign-in attempt (issue #737).

    No recipient input, so it cannot relay mail. Failures: 503 (mailer not
    configured / secret store unreachable) or 502 with machine-readable
    `detail.stage` in connect/tls/auth/send. Throttled per admin (#1147,
    `ADMIN_EMAIL_PREFLIGHT_PER_10MIN`): over the cap is a real 429, and the
    charge lands BEFORE the send — a failed attempt still spends a slot.
    """
    # Before the mailer is even constructed — the point is not to reach the relay.
    svc.enforce_preflight_quota(current_user.id)
    mailer = OtpMailer(secret_store)
    mailer.send_preflight(to=current_user.email)
    return AuthEmailTestResponse(to=current_user.email)


# ──────────────────── outbound-LLM provider config (ADR 0042, #1511) ────────────────────


class LlmSettingsRead(ApiModel):
    configured: bool
    provider: str | None = None
    base_url: str | None = None
    model: str | None = None
    structured_output: str | None = None
    enabled: bool = False
    #: Whether a credential is stored — the value itself is never returned.
    has_credential: bool = False
    updated_at: datetime | None = None


class LlmSettingsUpdate(ApiRequestModel):
    provider: Literal["anthropic", "openai_compatible"]
    model: str
    base_url: str | None = None
    #: Write-only; omit to keep the stored credential (refused if the destination moved).
    api_key: str | None = None
    structured_output: Literal["native", "prompt_json"] = "native"
    enabled: bool = True


class LlmTestResponse(ApiModel):
    ok: bool
    model: str | None = None
    latency_ms: int | None = None
    reply_chars: int | None = None
    error_code: str | None = None
    error: str | None = None


def _llm_settings_read(row: Any) -> LlmSettingsRead:
    if row is None:
        return LlmSettingsRead(configured=False)
    return LlmSettingsRead(
        configured=True,
        provider=row.provider,
        base_url=row.base_url,
        model=row.model,
        structured_output=row.structured_output,
        enabled=row.enabled,
        has_credential=row.api_key_secret_ref is not None,
        updated_at=row.updated_at,
    )


@router.get("/llm", response_model=LlmSettingsRead, summary="Outbound-LLM provider config (admin)")
def get_llm_settings(db: Annotated[Session, Depends(get_db)]) -> LlmSettingsRead:
    return _llm_settings_read(llm_service.get_settings_row(db))


@router.put(
    "/llm", response_model=LlmSettingsRead, summary="Save the outbound-LLM provider (admin)"
)
def put_llm_settings(
    payload: LlmSettingsUpdate,
    current_user: Annotated[User, Depends(require_workspace_admin)],
    db: Annotated[Session, Depends(get_db)],
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
) -> LlmSettingsRead:
    row = llm_service.save_settings(
        db,
        draft=llm_service.LlmSettingsDraft(
            provider=payload.provider,
            model=payload.model,
            base_url=payload.base_url,
            api_key=payload.api_key,
            structured_output=payload.structured_output,
            enabled=payload.enabled,
        ),
        actor=current_user,
        secret_store=secret_store,
    )
    db.commit()
    return _llm_settings_read(row)


@router.post(
    "/llm/test",
    response_model=LlmTestResponse,
    summary="Live-probe an LLM config draft — nothing persisted (admin)",
)
def test_llm_settings(
    payload: LlmSettingsUpdate,
    db: Annotated[Session, Depends(get_db)],
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
) -> LlmTestResponse:
    return LlmTestResponse(
        **llm_service.test_settings(
            db,
            draft=llm_service.LlmSettingsDraft(
                provider=payload.provider,
                model=payload.model,
                base_url=payload.base_url,
                api_key=payload.api_key,
                structured_output=payload.structured_output,
                enabled=payload.enabled,
            ),
            secret_store=secret_store,
        )
    )


# ───────────────────────── audit log (ADR 0041, #1318) ─────────────────────────


class AuditEventRead(ApiModel):
    """One audit event."""

    id: str
    occurred_at: str
    action_class: str
    action: str
    entity_type: str
    entity_id: str | None
    actor_user_id: str | None
    actor_kind: str
    actor_label: str | None
    actor_display: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    request_id: str | None


class AuditEventPage(ApiModel):
    """A page of events plus the fields needed to interpret it honestly."""

    events: list[AuditEventRead]
    total: int
    truncated: bool
    #: The configured retention window and the point before which events have been swept (`null`
    #: when the sweep is disabled).
    retention_days: int
    retained_since: datetime | None


def _assume_utc(value: datetime | None) -> datetime | None:
    """Interpret a naive datetime as UTC. See the note in `list_audit_events`."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


@router.get(
    "/audit-events",
    response_model=AuditEventPage,
    summary="Query the append-only audit log (workspace-admin only)",
)
def list_audit_events(
    db: Annotated[Session, Depends(get_db)],
    action_class: Literal["config", "access"] | None = None,
    entity_type: str | None = None,
    entity_id: UUID | None = None,
    actor_user_id: UUID | None = None,
    action: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> AuditEventPage:
    """The durable record of deliberate acts by a principal, newest first."""
    # A naive datetime compared against a `timestamptz` column is interpreted in the database
    # session's `TimeZone`.
    since = _assume_utc(since)
    until = _assume_utc(until)

    page = audit_read_service.list_events(
        db,
        action_class=action_class,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_user_id=actor_user_id,
        action=action,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return AuditEventPage(
        events=[AuditEventRead(**audit_read_service.as_dict(e)) for e in page.events],
        total=page.total,
        truncated=page.truncated,
        retention_days=page.retention_days,
        retained_since=page.retained_since,
    )


class AuditChainStatus(ApiModel):
    """The verification tooling ADR 0041 §9 / #1460 requires — not just a
    boolean, so an admin can see WHAT is or isn't covered.
    """

    #: "ok" (every hashed row verified) | "broken" (a mismatch was found) |
    #: "empty" (no hashed rows exist yet — nothing to verify, not the same as "ok").
    status: Literal["ok", "broken", "empty"]
    verified_count: int
    #: Rows written before the chain shipped — real audit history, just not covered
    #: by it. Reported, never silently folded into `verified_count`.
    unverifiable_legacy_count: int
    chain_head_hash: str | None
    #: Whether TAMPER_ANCHOR is configured. "ok" with anchor_mode="none" means the
    #: chain is internally consistent but NOT independently verifiable by anyone
    #: who could also rewrite the whole table — see ADR 0041 §9.
    anchor_mode: Literal["none", "webhook"]
    first_break: dict[str, Any] | None


@router.get(
    "/audit-events/verify",
    response_model=AuditChainStatus,
    summary="Verify the audit hash chain for tampering (workspace-admin only)",
)
def verify_audit_chain(db: Annotated[Session, Depends(get_db)]) -> AuditChainStatus:
    """The verification tooling for the tamper-evidence hash chain (#1460)."""
    result = audit_chain.verify_chain(db)
    if result.first_break is not None:
        status: Literal["ok", "broken", "empty"] = "broken"
    elif result.verified_count == 0:
        status = "empty"
    else:
        status = "ok"
    break_info = (
        None
        if result.first_break is None
        else {
            "event_id": str(result.first_break.event_id),
            "occurred_at": (
                result.first_break.occurred_at.isoformat()
                if result.first_break.occurred_at is not None
                else None
            ),
            "expected_prev_hash": result.first_break.expected_prev_hash,
            "actual_prev_hash": result.first_break.actual_prev_hash,
        }
    )
    settings = get_settings()
    return AuditChainStatus(
        status=status,
        verified_count=result.verified_count,
        unverifiable_legacy_count=result.unverifiable_legacy_count,
        chain_head_hash=result.chain_head_hash,
        anchor_mode=settings.tamper_anchor,
        first_break=break_info,
    )


# ───────────────────── deployment posture (G4 / #434) ──────────────────────


class ExternalTransfer(ApiModel):
    """One way data can leave the declared jurisdiction."""

    name: str
    enabled: bool
    detail: str


class DeploymentPostureRead(ApiModel):
    """What an auditor needs to answer "where does our data live, and what can
    take it elsewhere?" without shell access to the deployment.
    """

    #: The jurisdiction this deployment declares (`DEPLOYMENT_REGION`).
    region: str | None
    #: Ways data can leave that jurisdiction, each with whether it is live here.
    external_transfers: list[ExternalTransfer]


def _llm_intelligence_transfer(db: Session) -> ExternalTransfer:
    """The outbound-LLM posture row (ADR 0042): honest in BOTH states."""
    row = llm_service.get_settings_row(db)
    if row is not None and row.enabled:
        return ExternalTransfer(
            name="llm_intelligence",
            enabled=True,
            detail=(
                f"The OUTBOUND direction — DataQ calling a model on its own behalf. "
                f"Configured to a {row.provider} endpoint (model {row.model}), by an "
                "admin, with the customer's own credential (ADR 0042). Prompt "
                "context is schema plus masked aggregate profiler statistics — "
                "never sample rows; every call is recorded in llm_invocations "
                "with requester and token counts. Distinct from mcp_ai_clients "
                "above, which is inbound."
            ),
        )
    return ExternalTransfer(
        name="llm_intelligence",
        enabled=False,
        detail=(
            "The OUTBOUND direction — DataQ calling a model on its own behalf — "
            "is built (ADR 0042) but not configured or not enabled: no admin has "
            "added a provider, so nothing leaves. When enabled it is a Ch. V "
            "transfer by construction (schema-only context, PII-redacted, "
            "local-endpoint option). Listed while disabled on purpose: an "
            "auditor should see it was considered, not infer its absence. "
            "Distinct from mcp_ai_clients above, which is inbound."
        ),
    )


@router.get(
    "/deployment",
    response_model=DeploymentPostureRead,
    summary="Declared data residency and external-transfer vectors (workspace-admin only)",
)
def get_deployment_posture(db: Annotated[Session, Depends(get_db)]) -> DeploymentPostureRead:
    """The declared residency posture (GDPR Ch. V, G4/#434)."""
    settings = get_settings()
    # Per-suite notification configs count too.
    per_suite_alerting = bool(
        db.scalar(
            select(func.count())
            .select_from(SuiteNotification)
            .where(
                SuiteNotification.enabled.is_(True),
                or_(
                    SuiteNotification.webhook_secret_ref.isnot(None),
                    SuiteNotification.slack_webhook_secret_ref.isnot(None),
                    SuiteNotification.email_recipients.isnot(None),
                ),
            )
        )
    )
    transfers = [
        ExternalTransfer(
            name="alert_delivery",
            enabled=bool(
                settings.teams_webhook_secret_name
                or settings.slack_webhook_secret_name
                or settings.email_to
            )
            or per_suite_alerting,
            detail=(
                "Alerts carry check names, statuses and — when a failing sample is "
                "included — redacted sample values, to whatever webhook or mailbox "
                "the operator configured. That endpoint's own location is outside "
                "DataQ's knowledge, so this is a transfer whose destination only "
                "the operator can attest to."
            ),
        ),
        # TWO distinct LLM vectors: the unbuilt outbound one was listed while the LIVE
        # inbound one — third-party AI clients reading through /mcp — was NOT.
        ExternalTransfer(
            name="mcp_ai_clients",
            enabled=mcp_enabled(settings),
            detail=(
                "The /mcp surface serves run results, redacted failing samples and "
                "check configuration to whatever AI client holds a valid PAT — "
                "Claude Desktop, Copilot, Cursor. The model provider behind that "
                "client, and its jurisdiction, are chosen by the token holder and "
                "are outside DataQ's knowledge. This is a live transfer path today, "
                "not a future one, and it is the more consequential of the two "
                "LLM entries here."
            ),
        ),
        _llm_intelligence_transfer(db),
        ExternalTransfer(
            name="signin_email",
            enabled=bool(settings.auth_email_smtp_host),
            detail=(
                "Email-OTP sign-in (ADR 0032) sends one-time codes to user "
                "addresses through the configured SMTP relay. It carries account "
                "identifiers — personal data under GDPR Art 4(1) — rather than "
                "warehouse content, and the relay is operator-chosen."
            ),
        ),
        ExternalTransfer(
            name="secret_store",
            enabled=settings.secret_store not in {"redis", "memory"},
            detail=(
                f"Warehouse credentials are held in the '{settings.secret_store}' "
                "backend. Not customer data, but a remote store is a location "
                "outside the app's own region if configured that way — and the "
                "credentials it holds unlock the systems the customer data lives in."
            ),
        ),
        ExternalTransfer(
            name="telemetry",
            enabled=bool(
                settings.applicationinsights_connection_string
                or settings.otel_exporter_otlp_endpoint
            ),
            detail=(
                "Traces and logs to the configured backend. PII is redacted at the "
                "logger (core/logging.py), so this carries operational metadata "
                "rather than warehouse values — but the sink is chosen by the "
                "operator and may sit in another jurisdiction."
            ),
        ),
    ]
    return DeploymentPostureRead(
        region=settings.deployment_region or None,
        external_transfers=transfers,
    )


# ───────── data-subject-rights machinery (G2 / #432, GDPR Art 15/17/20, CCPA) ─────────


class DataSubjectRequest(ApiRequestModel):
    """The (column, value) pair identifying a subject's warehouse row — DataQ has
    no people-table, so this IS the subject identifier.
    """

    column: str
    value: str


class DataSubjectMatch(ApiModel):
    result_id: UUID
    run_id: UUID
    suite_id: UUID
    suite_name: str
    check_id: UUID
    check_name: str
    created_at: datetime
    matched_in: list[str]
    #: Deliberately UNREDACTED — this endpoint IS the subject's own access/export
    #: right (GDPR Art 15/20); the read-path redaction ladder exists to protect
    #: OTHER people's data from an unrelated viewer, not this one.
    sample_failures: dict[str, Any] | None
    observed_value: dict[str, Any] | None


class DataSubjectExportResponse(ApiModel):
    column: str
    value: str
    match_count: int
    matches: list[DataSubjectMatch]


@router.post(
    "/data-subject-requests/export",
    response_model=DataSubjectExportResponse,
    summary="Export a data subject's captured sample data (admin only, GDPR Art 15/20)",
)
def export_data_subject(
    payload: DataSubjectRequest,
    current_user: Annotated[User, Depends(require_workspace_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> DataSubjectExportResponse:
    """Workspace-wide export of every captured sample cell naming
    `column` = `value` — the access/portability half of the subject-rights
    machinery. Returns unredacted data by design (see `DataSubjectMatch`), so this
    is deliberately Admin-only, same tier as a connection credential.
    """
    matches = data_subject_requests.find_matching_results(
        db, column=payload.column, value=payload.value
    )
    audit_service.record_access(
        db,
        action="data_subject_request.export",
        entity_type="data_subject_request",
        entity_id=None,
        actor=current_user,
        exposed=bool(matches),
        detail={"column": payload.column, "match_count": len(matches)},
    )
    return DataSubjectExportResponse(
        column=payload.column,
        value=payload.value,
        match_count=len(matches),
        matches=[
            DataSubjectMatch(
                result_id=m.result_id,
                run_id=m.run_id,
                suite_id=m.suite_id,
                suite_name=m.suite_name,
                check_id=m.check_id,
                check_name=m.check_name,
                created_at=m.created_at,
                matched_in=list(m.matched_in),
                sample_failures=m.sample_failures,
                observed_value=m.observed_value,
            )
            for m in matches
        ],
    )


class DataSubjectErasureResponse(ApiModel):
    column: str
    value: str
    matched_count: int
    erased_count: int


@router.post(
    "/data-subject-requests/erase",
    response_model=DataSubjectErasureResponse,
    summary="Erase a data subject's captured sample data (admin only, GDPR Art 17 / CCPA delete)",
)
def erase_data_subject(
    payload: DataSubjectRequest,
    current_user: Annotated[User, Depends(require_workspace_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> DataSubjectErasureResponse:
    """Workspace-wide, on-demand erasure of every captured sample cell naming
    `column` = `value` — surgical (only the matching row/cell), not a blanket purge;
    see `data_subject_requests.erase_matching_results`. Runs synchronously and
    records one `audit_events` row inside the same transaction as the scrub, so a
    failed write leaves nothing behind and an applied one cannot go unrecorded
    (mirrors the ADR 0041 phase-1 mutation pattern).
    """
    summary = data_subject_requests.erase_matching_results(
        db, column=payload.column, value=payload.value
    )
    audit_service.record(
        db,
        action="data_subject_request.erase",
        entity_type="data_subject_request",
        entity_id=None,
        actor=current_user,
        after={
            "column": payload.column,
            "matched_count": len(summary.matched_result_ids),
            "erased_count": summary.erased_count,
        },
    )
    db.commit()
    return DataSubjectErasureResponse(
        column=payload.column,
        value=payload.value,
        matched_count=len(summary.matched_result_ids),
        erased_count=summary.erased_count,
    )
