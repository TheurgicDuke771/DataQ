"""Workspace-admin endpoints — the all-suites / all-users / access overview the
Admin page consumes, plus the SMTP pre-flight test (#737).

Every route is gated by `require_workspace_admin` (config allowlist), declared
once at the router so a non-admin gets a real 403. The read endpoints bypass
the owned-or-shared scoping `list_suites` applies — that's the point of the
page — and add no new authz on the per-suite ladder. `POST /auth-email/test`
is the one side-effecting route (it sends a real email); it's still read-only
from the DATABASE's point of view — no row is written. It is also the one route
with a throttle of its own (#1147), because "admin-gated" bounds *who* can open a
connection to the mail relay, not *how many*.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import ConfigDict
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel
from backend.app.core.auth import require_workspace_admin
from backend.app.core.config import get_settings
from backend.app.core.secrets import SecretStore, get_secret_store
from backend.app.db.models import SuiteNotification, User
from backend.app.db.session import get_db
from backend.app.mcp.auth import mcp_enabled
from backend.app.services import admin_service as svc
from backend.app.services import audit_read_service
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
    #: `None` once the creating user is erased (#1319). The suite stays listed —
    #: it still runs and still holds shares — so the admin overview must be able
    #: to render an ownerless one rather than 500 on serialization.
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
    #: The STORED role — what the editor below writes. Not the effective role:
    #: see `admin_service.AdminUserRow` for why showing the resolved value here
    #: would misrepresent exactly the rows an admin is most likely to act on.
    role: str
    #: Whether WORKSPACE_ADMIN_EMAILS grants this user admin regardless of the
    #: stored role, so the UI can explain a `member` row that is nonetheless an
    #: admin — and the last-admin guard's refusal alongside it.
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


class UserRoleUpdate(ApiModel):
    """`PATCH /admin/users/{id}/role` body (ADR 0033, #742)."""

    #: A Literal, not a bare `str`: an unknown tier is a 422 from the framework
    #: rather than something the service has to reject, and it puts the closed
    #: vocabulary in the OpenAPI schema where a client can read it. The service
    #: re-validates anyway — it is callable directly and must not depend on a
    #: router having filtered its input.
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
    """Set `user_id`'s stored workspace role — the one sanctioned way to demote.

    Admin-gated by the router-level dependency; `current_user` is re-declared
    here (rather than relying on that dependency alone) because the audit line
    needs the actor, and an audit line whose actor came from anywhere other than
    the authenticated principal would be worth less than none.

    Refuses rather than silently no-ops when a change cannot hold: the last
    stored-role admin, and the dev-bypass identity. See `admin_service.set_user_role`.
    """
    svc.set_user_role(db, user_id, new_role=payload.role, actor=current_user)
    # Re-read through the SAME row builder the list uses, so the response carries
    # the identical computed fields (`allowlist_admin`, the suite counts) — a
    # response shaped differently from the list it updates is how a table ends up
    # with one row rendering unlike its neighbours. Scoped to this user (not a
    # filter over the whole workspace aggregate), and it raises a typed 404
    # rather than a bare `StopIteration` if the row vanished between the write
    # and the read — an unhandled StopIteration surfaces as a 500 with no code.
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

    Sends to the admin's own address, never an arbitrary one — there is no
    recipient input on this endpoint, so it cannot be used to relay mail to a
    third party. A plain `def` (not `async def`): the blocking SMTP round trip
    below runs on Starlette's threadpool, the same offload FastAPI already gives
    every other sync path operation in this app (e.g. `auth_otp.request_otp`),
    so the event loop is never blocked on the mail server.

    Failure surfaces as `OtpMailNotConfiguredError` / `OtpMailStoreUnavailableError`
    (503 — the mailer isn't set up, or the secret store is unreachable) or
    `OtpMailPreflightError` (502, `detail.stage` in connect/tls/auth/send — the
    transport reached the relay but a specific stage failed). Both are `DataQError`
    subclasses, so they render through the standard error envelope automatically.

    **Throttled per admin, on top of the generic rate-limit class** (#1147):
    `ADMIN_EMAIL_PREFLIGHT_PER_10MIN` calls per 10-minute window, keyed on the
    caller's user id, over the same counter-store seam as the sign-in cap but a
    separate key space. Over the cap is a real `429` (`preflight_rate_limited`) —
    there is no anti-enumeration reason to soften it here, unlike `otp/request`.
    The charge happens **before** the send, so a failed submission still spends a
    slot: the quantity being bounded is connections opened at the relay. Fails
    open if the counter store is down. See `admin_service.enforce_preflight_quota`.
    """
    # Before the mailer is even constructed — the point is not to reach the relay.
    svc.enforce_preflight_quota(current_user.id)
    mailer = OtpMailer(secret_store)
    mailer.send_preflight(to=current_user.email)
    return AuthEmailTestResponse(to=current_user.email)


# ───────────────────────── audit log (ADR 0041, #1318) ─────────────────────────


class AuditEventRead(ApiModel):
    """One audit event.

    `actor_label` and `actor_display` are both served, deliberately. They are the
    same string almost always, and they differ exactly when the actor has been
    renamed or deleted since the event — `actor_label` is the identity **as at the
    time of the action**, `actor_display` resolves the live row. That difference
    is information an auditor wants ("this was done by someone who no longer
    exists"), not a discrepancy to paper over by serving one of them.
    """

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
    """A page of events plus the fields needed to interpret it honestly.

    `total` and `truncated` are not decoration: a page of `limit` rows is
    otherwise indistinguishable from "that is all there is", and on an audit log
    "there are no more events" is a conclusion someone may act on.
    """

    events: list[AuditEventRead]
    total: int
    truncated: bool
    #: The configured retention window and the point before which events have been
    #: swept (`null` when the sweep is disabled). Pagination honesty is not the
    #: only honesty this page needs: a query for a window older than retention
    #: returns `total: 0`, which is indistinguishable from "nothing happened
    #: then" — the single most misleading answer an audit log can give.
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
    """The durable record of deliberate acts by a principal, newest first.

    Workspace-admin only, via the router-level `require_workspace_admin` — there
    is deliberately no per-suite scoping. An audit log scoped by the grants of the
    person reading it cannot answer the question it exists for ("who changed the
    thing I no longer have access to?"), and every row here is *metadata about an
    act*, never warehouse data: `before`/`after` are built from a per-entity
    allow-list that excludes `sample_failures`, `observed_value` and every
    credential.

    Both `action_class` values are live. `config` records deliberate changes to
    the workspace; `access` records data *reads* (G1/#431 phase 2) — who read
    which run's results, when, and whether regulated data was actually surfaced.
    `access` covers exactly three doors today: reading a run's results over REST,
    the same read over MCP, and downloading a comparison report. **An empty
    `action_class=access` page is therefore evidence about those doors only, and
    is NOT evidence that no regulated data was surfaced.** Two routes return live
    warehouse values and record nothing here — the column profiler (`top_values`
    holds real cell values) and the check dry-run (`observed_value`, unredacted
    per #1419). An investigator who reads an empty page as "nobody saw anything"
    would be wrong in precisely the way this endpoint must not be wrong.

    Three further things an empty page does not rule out. Reads older than
    `AUDIT_RETENTION_DAYS` (default 365) have been swept — the response states
    its own window in `retention_days`/`retained_since` rather than leaving that
    to be assumed. A read whose audit write failed still succeeded, because the
    access path is deliberately not fail-closed (ADR 0041), so a gap correlates
    with `audit_access_write_failed` at ERROR in telemetry. And warehouse-side
    access made outside DataQ is invisible here by definition.

    This paragraph previously said `access` was unbuilt and that an empty page
    meant "not built yet, not nobody read anything". It is kept explicit about
    coverage because the *shape* of that error — a confident claim that inverts
    an auditor's conclusion — is the failure mode of this particular endpoint.
    """
    # A naive datetime compared against a `timestamptz` column is interpreted in
    # the database session's `TimeZone`, so the window a caller asked for would
    # silently shift with server configuration — and an audit query that quietly
    # covers a different period than requested is worse than one that refuses.
    # UTC is the assumption because every timestamp this API emits is UTC ISO-8601.
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


# ───────────────────── deployment posture (G4 / #434) ──────────────────────


class ExternalTransfer(ApiModel):
    """One way data can leave the declared jurisdiction."""

    name: str
    enabled: bool
    detail: str


class DeploymentPostureRead(ApiModel):
    """What an auditor needs to answer "where does our data live, and what can
    take it elsewhere?" without shell access to the deployment.

    Deliberately reports **declarations and configuration**, never a probe. The
    app cannot verify which datacentre its database sits in, and a field that
    looked like verification would be worse than one that is plainly a
    declaration — it would be believed.
    """

    #: The jurisdiction this deployment declares (`DEPLOYMENT_REGION`). `null`
    #: when unset, which reads as "not declared" rather than implying a default —
    #: an auditor should see the gap, not a guess.
    region: str | None
    #: Ways data can leave that jurisdiction, each with whether it is live here.
    external_transfers: list[ExternalTransfer]


@router.get(
    "/deployment",
    response_model=DeploymentPostureRead,
    summary="Declared data residency and external-transfer vectors (workspace-admin only)",
)
def get_deployment_posture(db: Annotated[Session, Depends(get_db)]) -> DeploymentPostureRead:
    """The declared residency posture (GDPR Ch. V, G4/#434).

    Workspace-admin only via the router gate. The transfer list is enumerated
    rather than derived, so a new outbound integration has to be added here
    consciously — a vector that reports itself only when someone remembers is not
    a control an auditor can rely on.
    """
    settings = get_settings()
    # Per-suite notification configs count too. Reading only the WORKSPACE
    # settings reported "no alert transfer" for a deployment that alerts entirely
    # through per-suite webhooks — which is a supported and common shape, and
    # exactly the deployment whose operator most needs the register to be right.
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
        # TWO distinct LLM vectors, and conflating them was a real omission: the
        # unbuilt outbound one was listed while the LIVE inbound one — third-party
        # AI clients reading through /mcp — was not.
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
        ExternalTransfer(
            name="llm_intelligence",
            enabled=False,
            detail=(
                "The OUTBOUND direction — DataQ calling a model on its own behalf — "
                "and it is not built. When it lands it is a Ch. V transfer by "
                "construction; its design posture (schema-only context, "
                "PII-redacted, local-endpoint option) is in "
                "docs/post-v1-dq-intelligence-notes.md. Listed while disabled on "
                "purpose: an auditor should see it was considered, not infer its "
                "absence. Distinct from mcp_ai_clients above, which is inbound "
                "and live."
            ),
        ),
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
