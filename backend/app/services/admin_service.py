"""Workspace-admin read queries — the all-suites / all-users / access overview
behind the Admin page, plus the SMTP pre-flight throttle (#1147).
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

from backend.app.core.auth import DEV_BYPASS_AAD_OID, DEV_BYPASS_EMAIL
from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretNotFoundError, SecretStore
from backend.app.db.models import (
    ADMIN_ROLE,
    ORCHESTRATION_PROVIDERS,
    WORKSPACE_ROLES,
    Check,
    Connection,
    Share,
    Suite,
    User,
)
from backend.app.services import audit_service, otp_service
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
    #: `None` once the creating user is erased (`created_by` is `SET NULL`, #1319).
    owner_id: UUID | None
    owner_email: str | None
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
    #: The STORED `users.role`, not the effective one.
    role: str
    #: True when `WORKSPACE_ADMIN_EMAILS` grants this user admin regardless of their stored role.
    allowlist_admin: bool


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
    """Every suite with owner + datasource + check/share counts, newest first."""
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
        # OUTER join on the author: a suite whose creator was erased must stay visible.
        .outerjoin(User, Suite.created_by == User.id)
        .join(Connection, Suite.connection_id == Connection.id)
        .outerjoin(Check, Check.suite_id == Suite.id)
        .outerjoin(Share, Share.suite_id == Suite.id)
        # Group by each table's PK (Postgres lets us select its other columns).
        .group_by(Suite.id, Connection.id, User.id)
        .order_by(Suite.created_at.desc())
    )
    return [AdminSuiteRow(*row) for row in session.execute(stmt)]


def get_admin_user(session: Session, user_id: UUID) -> AdminUserRow:
    """One user's admin-overview row — the same shape `list_all_users` returns."""
    rows = _admin_user_rows(session, user_id=user_id)
    if not rows:
        raise UserNotFoundError("user not found", detail={"user_id": str(user_id)})
    return rows[0]


def list_all_users(session: Session) -> list[AdminUserRow]:
    """Every user with their owned-suite and shared-suite counts, by email."""
    return _admin_user_rows(session)


def _admin_user_rows(session: Session, *, user_id: UUID | None = None) -> list[AdminUserRow]:
    """The one row-builder behind both the list and the single-user read."""
    stmt = (
        select(
            User.id,
            User.email,
            User.display_name,
            User.last_seen_at,
            User.created_at,
            func.count(func.distinct(Suite.id)).label("owned_suite_count"),
            func.count(func.distinct(Share.id)).label("shared_suite_count"),
            User.role,
        )
        .outerjoin(Suite, Suite.created_by == User.id)
        .outerjoin(Share, Share.user_id == User.id)
        .group_by(User.id)
        .order_by(User.email)
    )
    if user_id is not None:
        stmt = stmt.where(User.id == user_id)
    settings = get_settings()
    # Named rather than splatted: `allowlist_admin` is NOT a column — the allowlist lives in env,
    # not in Postgres — so it cannot ride in the SELECT.
    return [
        AdminUserRow(
            id=row.id,
            email=row.email,
            display_name=row.display_name,
            last_seen_at=row.last_seen_at,
            created_at=row.created_at,
            owned_suite_count=row.owned_suite_count,
            shared_suite_count=row.shared_suite_count,
            role=row.role,
            allowlist_admin=settings.is_admin_email(row.email),
        )
        for row in session.execute(stmt)
    ]


def list_all_access(session: Session) -> list[AdminAccessRow]:
    """Full access matrix: every implicit owner + every explicit share row."""
    # Outer join for the same reason as `list_all_suites`: after an erasure the suite has no owner
    # ROW, and an inner join would report that as "this suite has no owner grant".
    owner_stmt = select(Suite.id, Suite.name, User.id, User.email, User.display_name).outerjoin(
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
        # An erased author leaves no grant to report: the suite has no owner, and a row with a null
        # user would render as a grant to nobody.
        if uid is not None
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
    """One orchestration provider's inbound-webhook setup for the admin UI (#490)."""

    provider: str
    auth: str
    inbound_url: str
    token_configured: bool
    signing_secret_name: str | None
    connection_names: list[str]


def _safe_secret(secret_store: SecretStore, name: str) -> str | None:
    """Resolve a secret, returning None if it isn't provisioned (so the webhook
    surface degrades to a clear 'not set' marker instead of erroring).
    """
    try:
        return secret_store.get(name)
    except SecretNotFoundError:
        return None


def webhook_configs(
    session: Session, *, base_url: str, secret_store: SecretStore
) -> list[WebhookConfigRow]:
    """Inbound-webhook config per orchestration provider that has a connection."""
    base = base_url.rstrip("/")
    names_by_provider: dict[str, list[str]] = {}
    for conn in session.scalars(
        select(Connection)
        .where(Connection.type.in_(ORCHESTRATION_PROVIDERS))
        .order_by(Connection.type, Connection.name)
    ):
        names_by_provider.setdefault(conn.type, []).append(conn.name)

    settings = get_settings()
    # The HMAC-callback providers share a row shape; only the signing key and the ADR differ.
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
            # URL-encode the secret: the receiver reads `token` URL-decoded.
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


# ── SMTP pre-flight throttle (#1147) ───────────────────────────────────────── `POST /admin/auth-
# email/test` makes a real outbound SMTP connection on every call.

#: The pre-flight window.
PREFLIGHT_WINDOW_SECONDS = 600

#: This throttle's OWN store instance — never `otp_service`'s module singleton (see
#: the section header). Same class, same Redis, independent breaker + client.
_preflight_store: otp_service.OtpCounterStore | None = None
_preflight_store_unavailable_warned = False


def get_preflight_counter_store() -> otp_service.OtpCounterStore:
    global _preflight_store
    if _preflight_store is None:
        _preflight_store = otp_service.RedisOtpCounterStore(get_settings().redis_url)
    return _preflight_store


def set_preflight_counter_store_for_testing(store: otp_service.OtpCounterStore | None) -> None:
    """Test hook mirroring `otp_service.set_counter_store_for_testing`."""
    global _preflight_store, _preflight_store_unavailable_warned
    _preflight_store = store
    _preflight_store_unavailable_warned = False


def reset_preflight_counter_state() -> None:
    """Test hook mirroring `otp_service.reset_counter_state`. Called by conftest
    around every test: left set, an injected in-memory store carries counts into
    unrelated tests; left unset after one was injected, a later test reaches for a
    real Redis client on an admin path.
    """
    set_preflight_counter_store_for_testing(None)


class PreflightThrottledError(DataQError):
    """Too many SMTP pre-flight tests from one admin in the window — a real 429."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(
            "Too many SMTP pre-flight tests. Each one opens a real connection to "
            "your mail relay — wait for the window to reset before trying again.",
            code="preflight_rate_limited",
            status_code=429,
            detail={"retry_after_seconds": retry_after_seconds},
        )


def _preflight_key(user_id: UUID, *, now: float) -> str:
    """`preflight:<sha256(admin id)>:<window>`."""
    digest = hashlib.sha256(str(user_id).encode()).hexdigest()[:32]
    window = int(now) // PREFLIGHT_WINDOW_SECONDS
    return f"preflight:{digest}:{window}"


def enforce_preflight_quota(user_id: UUID, settings: Settings | None = None) -> None:
    """Charge this admin one pre-flight; raise `PreflightThrottledError` past the cap."""
    s = settings or get_settings()
    limit = s.admin_email_preflight_per_10min
    if limit <= 0:
        return  # 0 = off, with the documented risk re-accepted (see `Settings`).
    global _preflight_store_unavailable_warned
    now = time.time()
    count = get_preflight_counter_store().incr_window(
        _preflight_key(user_id, now=now), PREFLIGHT_WINDOW_SECONDS * 2
    )
    if count is None:
        if not _preflight_store_unavailable_warned:
            _preflight_store_unavailable_warned = True
            # No id and no key on this line — the key holds a stable per-admin
            # digest, and this must not lean on the logger's PII redaction.
            log.warning(
                "admin_preflight_counter_store_unavailable",
                window_seconds=PREFLIGHT_WINDOW_SECONDS,
            )
        return
    if count > limit:
        window_end = (int(now) // PREFLIGHT_WINDOW_SECONDS + 1) * PREFLIGHT_WINDOW_SECONDS
        log.warning(
            "admin_preflight_throttled", limit=limit, window_seconds=PREFLIGHT_WINDOW_SECONDS
        )
        raise PreflightThrottledError(max(1, int(window_end - now)))


# ── In-app role management (ADR 0033 decision 7, #742) ─────────────────────── The one sanctioned
# way to CHANGE a workspace role.


class RoleChangeRejectedError(DataQError):
    """A role change the workspace's invariants forbid — 409, never a silent no-op."""

    status_code = 409
    code = "role_change_rejected"


class UserNotFoundError(DataQError):
    status_code = 404
    code = "user_not_found"


def set_user_role(
    session: Session,
    user_id: UUID,
    *,
    new_role: str,
    actor: User,
) -> User:
    """Set a user's stored workspace role. Caller must already be admin-gated."""
    if new_role not in WORKSPACE_ROLES:
        raise RoleChangeRejectedError(
            f"unknown workspace role: {new_role!r}",
            detail={"role": new_role, "allowed": list(WORKSPACE_ROLES)},
        )

    # Existence first, so a bad id is a 404 rather than a lock wait.
    if session.get(User, user_id) is None:
        raise UserNotFoundError("user not found", detail={"user_id": str(user_id)})

    # ── Everything below decides from LOCKED state, and that is load-bearing ── An earlier cut read
    # `target.role` before taking the lock and gated the last-admin guard on that value.
    target = session.execute(
        select(User).where(User.id == user_id).with_for_update()
    ).scalar_one_or_none()
    if target is None:  # pragma: no cover — deleted between the check and the lock
        raise UserNotFoundError("user not found", detail={"user_id": str(user_id)})
    admin_ids = set(
        session.scalars(select(User.id).where(User.role == ADMIN_ROLE).with_for_update()).all()
    )
    previous = target.role

    if previous == new_role:
        # Idempotent, and deliberately NOT an error: a UI that re-submits the current value should
        # not surface a failure.
        return target

    if target.aad_object_id == DEV_BYPASS_AAD_OID or target.email == DEV_BYPASS_EMAIL:
        raise RoleChangeRejectedError(
            "the local dev-bypass identity's role cannot be changed — it is the "
            "single operator of a dev-bypass workspace and is always an admin",
            detail={"user_id": str(user_id)},
        )

    # `target.id in admin_ids`, not `previous == ADMIN_ROLE`: both now come from the same locked
    # snapshot.
    if target.id in admin_ids and admin_ids <= {target.id}:
        raise RoleChangeRejectedError(
            "cannot remove the last workspace admin — promote another user to "
            "admin first. (Admins granted only by WORKSPACE_ADMIN_EMAILS do not "
            "count: that allowlist is a recovery path, not the invariant.)",
            detail={"user_id": str(user_id), "stored_admin_count": len(admin_ids)},
        )

    target.role = new_role
    # The durable record (ADR 0041 phase 1, #1318).
    audit_service.record(
        session,
        action="user.role_change",
        entity_type="user",
        entity_id=target.id,
        actor=actor,
        before={"id": str(target.id), "role": previous},
        after={"id": str(target.id), "role": new_role},
    )
    session.commit()
    session.refresh(target)

    # This line is no longer the whole guarantee that a role change is never silent — `audit_events`
    # is — but it stays: it is what a live operator watching logs sees.
    log.info(
        "workspace_role_changed",
        actor_id=str(actor.id),
        target_user_id=str(user_id),
        previous_role=previous,
        new_role=new_role,
    )
    return target
