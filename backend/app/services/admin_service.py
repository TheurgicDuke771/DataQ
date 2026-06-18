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

from backend.app.db.models import Check, Connection, Share, Suite, User
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
