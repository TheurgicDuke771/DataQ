"""Workspace-admin read queries — the all-suites / all-users / access overview
behind the Admin page, plus the SMTP pre-flight throttle (#1147).

Deliberately *unscoped*: unlike `suite_service.list_suites` (owned-or-shared),
these return the whole workspace, so the API layer must gate them on
`require_workspace_admin`. Read-only, FastAPI-free (takes a `Session`).

The one exception to "read-only" is `enforce_preflight_quota` at the bottom, which
writes a counter rather than a row — see its section header for why the pre-flight
endpoint needs a budget of its own.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretNotFoundError, SecretStore
from backend.app.db.models import ORCHESTRATION_PROVIDERS, Check, Connection, Share, Suite, User
from backend.app.services import otp_service
from backend.app.services.suite_authz import OWNER

log = get_logger(__name__)

# Strongest-first permission rank for ordering the access overview.
_PERMISSION_RANK = {OWNER: 0, "admin": 1, "edit": 2, "view": 3}


@dataclass(frozen=True)
class AdminSuiteRow:
    """One suite in the admin overview, with its owner, datasource, and counts."""

    id: UUID
    name: str
    connection_name: str
    connection_type: str
    env: str
    owner_id: UUID
    owner_email: str
    owner_name: str | None
    check_count: int
    share_count: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class AdminUserRow:
    """One user in the admin overview, with how many suites they own / share in."""

    id: UUID
    email: str
    display_name: str | None
    last_seen_at: datetime | None
    created_at: datetime
    owned_suite_count: int
    shared_suite_count: int


@dataclass(frozen=True)
class AdminAccessRow:
    """One (user → suite) access grant: an implicit owner or an explicit share."""

    suite_id: UUID
    suite_name: str
    user_id: UUID
    user_email: str
    user_name: str | None
    permission: str  # 'owner' | 'admin' | 'edit' | 'view'


def list_all_suites(session: Session) -> list[AdminSuiteRow]:
    """Every suite with owner + datasource + check/share counts, newest first.

    Counts use `distinct` because the check and share outer-joins multiply rows.
    """
    stmt = (
        select(
            Suite.id,
            Suite.name,
            Connection.name,
            Connection.type,
            Connection.env,
            User.id,
            User.email,
            User.display_name,
            func.count(func.distinct(Check.id)),
            func.count(func.distinct(Share.id)),
            Suite.created_at,
            Suite.updated_at,
        )
        .join(User, Suite.created_by == User.id)
        .join(Connection, Suite.connection_id == Connection.id)
        .outerjoin(Check, Check.suite_id == Suite.id)
        .outerjoin(Share, Share.suite_id == Suite.id)
        # Group by each table's PK (Postgres lets us select its other columns).
        .group_by(Suite.id, Connection.id, User.id)
        .order_by(Suite.created_at.desc())
    )
    return [AdminSuiteRow(*row) for row in session.execute(stmt)]


def list_all_users(session: Session) -> list[AdminUserRow]:
    """Every user with their owned-suite and shared-suite counts, by email."""
    stmt = (
        select(
            User.id,
            User.email,
            User.display_name,
            User.last_seen_at,
            User.created_at,
            func.count(func.distinct(Suite.id)),
            func.count(func.distinct(Share.id)),
        )
        .outerjoin(Suite, Suite.created_by == User.id)
        .outerjoin(Share, Share.user_id == User.id)
        .group_by(User.id)
        .order_by(User.email)
    )
    return [AdminUserRow(*row) for row in session.execute(stmt)]


def list_all_access(session: Session) -> list[AdminAccessRow]:
    """Full access matrix: every implicit owner + every explicit share row.

    Ordered by suite name, then strongest permission first, then user email.
    """
    owner_stmt = select(Suite.id, Suite.name, User.id, User.email, User.display_name).join(
        User, Suite.created_by == User.id
    )
    share_stmt = (
        select(Suite.id, Suite.name, User.id, User.email, User.display_name, Share.permission)
        .join(Suite, Share.suite_id == Suite.id)
        .join(User, Share.user_id == User.id)
    )

    rows = [
        AdminAccessRow(sid, sname, uid, email, name, OWNER)
        for sid, sname, uid, email, name in session.execute(owner_stmt)
    ]
    rows += [
        AdminAccessRow(sid, sname, uid, email, name, perm)
        for sid, sname, uid, email, name, perm in session.execute(share_stmt)
    ]
    rows.sort(
        key=lambda r: (r.suite_name.lower(), _PERMISSION_RANK.get(r.permission, 9), r.user_email)
    )
    return rows


@dataclass(frozen=True)
class WebhookConfigRow:
    """One orchestration provider's inbound-webhook setup for the admin UI (#490).

    `inbound_url` is ready to paste into the provider's webhook field. For ADF it
    embeds the shared secret as the `?token=` query param (ADR 0006) — so this row
    is **secret-bearing**, only returned behind `require_workspace_admin`, and must
    never be logged. Airflow (ADR 0007) and dbt (ADR 0029) carry no URL secret
    (HMAC signature header); the signing key lives in the secret store under
    `signing_secret_name` and is configured in the callback snippet, not the URL.
    """

    provider: str
    auth: str
    inbound_url: str
    token_configured: bool
    signing_secret_name: str | None
    connection_names: list[str]


def _safe_secret(secret_store: SecretStore, name: str) -> str | None:
    """Resolve a secret, returning None if it isn't provisioned (so the webhook
    surface degrades to a clear 'not set' marker instead of erroring).

    Narrow to the store's not-found error (as the event receiver does) — an
    unexpected error still propagates rather than masquerading as 'not set'.
    """
    try:
        return secret_store.get(name)
    except SecretNotFoundError:
        return None


def webhook_configs(
    session: Session, *, base_url: str, secret_store: SecretStore
) -> list[WebhookConfigRow]:
    """Inbound-webhook config per orchestration provider that has a connection.

    Provider-level (one shared secret per provider), so one row per provider with
    ≥1 connection, listing the connections it covers. `base_url` is the public API
    base (scheme+host, no trailing slash). Secret-bearing for ADF — admin-only.
    """
    base = base_url.rstrip("/")
    names_by_provider: dict[str, list[str]] = {}
    for conn in session.scalars(
        select(Connection)
        .where(Connection.type.in_(ORCHESTRATION_PROVIDERS))
        .order_by(Connection.type, Connection.name)
    ):
        names_by_provider.setdefault(conn.type, []).append(conn.name)

    settings = get_settings()
    # The HMAC-callback providers share a row shape; only the signing key and the
    # ADR differ. A future provider missing here fails loudly (KeyError) instead
    # of being silently mislabeled as another provider (#647).
    hmac_providers: dict[str, tuple[str, str]] = {
        "airflow": (settings.airflow_webhook_secret_name, "ADR 0007"),
        "dbt": (settings.dbt_webhook_secret_name, "ADR 0029"),
    }
    rows: list[WebhookConfigRow] = []
    for provider in ORCHESTRATION_PROVIDERS:
        names = names_by_provider.get(provider, [])
        if not names:
            continue
        if provider == "adf":
            token = _safe_secret(secret_store, settings.adf_webhook_secret_name)
            # URL-encode the secret: the receiver reads `token` URL-decoded, so a
            # secret containing &/+/=/% must be percent-encoded or the pasted URL
            # won't match (ADR 0006). bool(token) (not `is not None`) so an empty
            # secret reads as not-configured, consistent with the placeholder.
            token_param = (
                quote(token, safe="")
                if token
                else f"<set {settings.adf_webhook_secret_name} in Key Vault>"
            )
            rows.append(
                WebhookConfigRow(
                    provider="adf",
                    auth="Shared secret in the URL (?token=…), constant-time checked — ADR 0006",
                    inbound_url=f"{base}/api/v1/orchestration/events/adf?token={token_param}",
                    token_configured=bool(token),
                    signing_secret_name=None,
                    connection_names=names,
                )
            )
        else:  # HMAC-signed callback providers (airflow, dbt)
            signing_secret_name, adr = hmac_providers[provider]
            # Honest configured-state: a hardcoded True here hid an unprovisioned
            # signing key until callbacks started failing auth at the receiver.
            signing_key = _safe_secret(secret_store, signing_secret_name)
            rows.append(
                WebhookConfigRow(
                    provider=provider,
                    auth=f"HMAC-SHA256 signature header (X-DataQ-Signature) — {adr}",
                    inbound_url=f"{base}/api/v1/orchestration/events/{provider}",
                    token_configured=bool(signing_key),
                    signing_secret_name=signing_secret_name,
                    connection_names=names,
                )
            )
    return rows


# ── SMTP pre-flight throttle (#1147) ─────────────────────────────────────────
#
# `POST /admin/auth-email/test` makes a real outbound SMTP connection on every
# call. It is admin-gated and can only ever mail the caller's own address, so it is
# not an open relay — but it sat in the generic authenticated class
# (`RATE_LIMIT_AUTHENTICATED_PER_MINUTE`, 300/min per token), which was sized for
# ordinary API traffic, not for an endpoint whose whole job is a third-party
# network call. A scripted or compromised admin token could burn hundreds of
# connections a minute at the configured relay, and relays throttle or block a
# sending account that behaves that way — which would take the REAL sign-in mailer
# down with it. That is a worse outage than the misconfiguration this endpoint
# exists to catch early.
#
# The mechanism is `otp_service`'s counter-store seam, reused rather than
# reinvented: the same `OtpCounterStore` Protocol, the same fixed-window
# INCR+EXPIRE, the same bounded socket timeouts and circuit breaker, the same
# fail-open bias. The KEYS are separate (`preflight:` vs `otp:req:`) and are keyed
# on the ADMIN rather than on a mailbox, so a pre-flight can never spend somebody's
# sign-in quota and a sign-in can never spend an admin's diagnostics. One shared
# mechanism, two independent budgets.

#: The pre-flight window. Ten minutes — its own constant, deliberately not a
#: reference to `otp_service.EMAIL_WINDOW_SECONDS`, because the two bound different
#: quantities (diagnostic calls by one admin vs live codes into one mailbox) and a
#: future change to either must not silently move the other.
PREFLIGHT_WINDOW_SECONDS = 600


class PreflightThrottledError(DataQError):
    """Too many SMTP pre-flight tests from one admin in the window — a real 429.

    A REAL 429, unlike `otp/request`'s deliberate uniform `ok`: the anti-enumeration
    argument that makes a throttle-shaped response an oracle *there* does not apply
    *here* at all. The caller is an already-authenticated, already-allow-listed
    workspace admin asking about their own configuration — there is no membership
    fact left to hide, and telling them plainly that they are hammering the relay is
    the useful answer.

    `retry_after_seconds` rides in the envelope's `detail`, mirroring the rate-limit
    middleware's 429 body. There is no `Retry-After` **header**: the shared
    `DataQError` handler renders body-only. Worth knowing if you are writing a
    client against this, and not worth a bespoke exception handler for one admin
    endpoint.
    """

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(
            "Too many SMTP pre-flight tests. Each one opens a real connection to "
            "your mail relay — wait for the window to reset before trying again.",
            code="preflight_rate_limited",
            status_code=429,
            detail={"retry_after_seconds": retry_after_seconds},
        )


def _preflight_key(user_id: UUID, *, now: float) -> str:
    """`preflight:<sha256(admin id)>:<window>`.

    Hashed for the same reason `otp_service._email_bucket_key` hashes: Redis keys
    are readable to anyone with `SCAN`, and a user id is a stable per-person
    identifier even though it is not a mailbox. The window index rides IN the key,
    so there is no read-modify-EXPIRE race.

    Keyed on the user **id**, not the email: it is what `require_workspace_admin`
    already resolved, it survives an address change, and it keeps a mailbox out of
    the key entirely.

    The `preflight:` prefix is what keeps this budget separate from the sign-in
    counter's `otp:req:` — the shared store is a mechanism, not a shared quota.
    """
    digest = hashlib.sha256(str(user_id).encode()).hexdigest()[:32]
    window = int(now) // PREFLIGHT_WINDOW_SECONDS
    return f"preflight:{digest}:{window}"


def enforce_preflight_quota(user_id: UUID, settings: Settings | None = None) -> None:
    """Charge this admin one pre-flight; raise `PreflightThrottledError` past the cap.

    Counted BEFORE the send, so a failed submission still spends a slot — what is
    being bounded is *connections opened at the relay*, and a relay that is already
    refusing us is exactly when a retry loop does the most damage.

    **Fails open** when the counter store is unavailable, matching
    `otp_service._within_email_quota` and the rate-limit middleware (ADR 0035's
    deliberate bias: availability over enforcement). A Redis outage must not take
    the operator's only mail-configuration diagnostic away from them at the moment
    they are most likely to need it.

    Unlike the sign-in counter this warns on EVERY fail-open rather than once per
    process: an admin makes a handful of these calls an hour, so there is no
    log-spam to suppress, and each line is a true statement about a diagnostic that
    ran unenforced.
    """
    s = settings or get_settings()
    limit = s.admin_email_preflight_per_10min
    if limit <= 0:
        return  # 0 = off, with the documented risk re-accepted (see `Settings`).
    now = time.time()
    count = otp_service.get_counter_store().incr_window(
        _preflight_key(user_id, now=now), PREFLIGHT_WINDOW_SECONDS * 2
    )
    if count is None:
        # No id and no key on this line — the key holds a stable per-admin digest,
        # and this must not lean on the logger's PII redaction to stay clean.
        log.warning(
            "admin_preflight_counter_store_unavailable", window_seconds=PREFLIGHT_WINDOW_SECONDS
        )
        return
    if count > limit:
        window_end = (int(now) // PREFLIGHT_WINDOW_SECONDS + 1) * PREFLIGHT_WINDOW_SECONDS
        log.warning(
            "admin_preflight_throttled", limit=limit, window_seconds=PREFLIGHT_WINDOW_SECONDS
        )
        raise PreflightThrottledError(max(1, int(window_end - now)))
