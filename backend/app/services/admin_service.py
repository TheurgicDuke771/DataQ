"""Workspace-admin read queries — the all-suites / all-users / access overview
behind the Admin page.

Deliberately *unscoped*: unlike `suite_service.list_suites` (owned-or-shared),
these return the whole workspace, so the API layer must gate them on
`require_workspace_admin`. Read-only, FastAPI-free (takes a `Session`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.core.secrets import SecretStore
from backend.app.db.models import ORCHESTRATION_PROVIDERS, Check, Connection, Share, Suite, User
from backend.app.services.suite_authz import OWNER

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
    never be logged. Airflow carries no URL secret (HMAC header, ADR 0007); the
    signing key lives in Key Vault under `signing_secret_name` and is configured in
    the DAG callback snippet, not the URL.
    """

    provider: str
    auth: str
    inbound_url: str
    token_configured: bool
    signing_secret_name: str | None
    connection_names: list[str]


def _safe_secret(secret_store: SecretStore, name: str) -> str | None:
    """Resolve a secret, returning None if it isn't provisioned (so the webhook
    surface degrades to a clear 'not set' marker instead of erroring)."""
    try:
        return secret_store.get(name)
    except Exception:
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
    rows: list[WebhookConfigRow] = []
    for provider in ORCHESTRATION_PROVIDERS:
        names = names_by_provider.get(provider, [])
        if not names:
            continue
        if provider == "adf":
            token = _safe_secret(secret_store, settings.adf_webhook_secret_name)
            token_param = (
                token if token else f"<set {settings.adf_webhook_secret_name} in Key Vault>"
            )
            rows.append(
                WebhookConfigRow(
                    provider="adf",
                    auth="Shared secret in the URL (?token=…), constant-time checked — ADR 0006",
                    inbound_url=f"{base}/api/v1/orchestration/events/adf?token={token_param}",
                    token_configured=token is not None,
                    signing_secret_name=None,
                    connection_names=names,
                )
            )
        else:  # airflow
            rows.append(
                WebhookConfigRow(
                    provider="airflow",
                    auth="HMAC-SHA256 signature header (X-DataQ-Signature) — ADR 0007",
                    inbound_url=f"{base}/api/v1/orchestration/events/airflow",
                    token_configured=True,
                    signing_secret_name=settings.airflow_webhook_secret_name,
                    connection_names=names,
                )
            )
    return rows
