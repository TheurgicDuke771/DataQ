"""Membership state and the cross-table last-admin guard, shared by admin_service and
membership_service so neither imports the other."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import exists, func, inspect, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.db.models import ADMIN_ROLE, User, WorkspaceMember

log = get_logger(__name__)


def _table_missing(db: Session, exc: ProgrammingError) -> bool:
    """Whether the failure was `workspace_members` not existing yet."""
    db.rollback()
    bind = db.get_bind()
    try:
        return not inspect(bind).has_table(WorkspaceMember.__tablename__)
    except Exception:  # pragma: no cover — the catalog read itself failed
        return False


def enforcement_active(db: Session, /) -> bool:
    """Whether any managed member exists — the switch itself (decision 3).

    A missing table reads as "not enforced" — what an empty one means — so an
    image rolled ahead of its migration does not 500 every request.
    """
    try:
        return bool(db.execute(select(exists().select_from(WorkspaceMember))).scalar())
    except ProgrammingError as exc:
        if not _table_missing(db, exc):
            raise
        log.warning("membership_table_missing", effect="membership is not enforced")
        return False


class RoleChangeRejectedError(DataQError):
    """A role change the workspace's invariants forbid — 409, never a silent no-op."""

    status_code = 409
    code = "role_change_rejected"


def assert_admin_remains(
    session: Session,
    *,
    exclude_user_id: UUID | None = None,
    exclude_member_id: UUID | None = None,
    lock: bool = True,
) -> None:
    """Refuse a change that would leave nobody who can both sign in and administer.

    The two axes are separate tables and were guarded separately, so removing
    B's membership and then demoting A each looked safe while together they
    emptied the workspace. One predicate over both, locking `users` before
    `workspace_members` so concurrent callers queue instead of deadlocking.

    Stored-role admins only: an allowlist-resolved admin can vanish with the
    next deploy, so it cannot be what keeps the workspace recoverable.

    `lock=False` is for READ paths (a preview): the answer is advisory there, and
    holding `FOR UPDATE` on every admin row for the life of a GET serialises the
    workspace's admin writes behind a screen (#1922).
    """
    admins_stmt = (
        select(User.id, func.lower(User.email)).where(User.role == ADMIN_ROLE).order_by(User.id)
    )
    if lock:
        admins_stmt = admins_stmt.with_for_update()
    admins = session.execute(admins_stmt).all()
    enforced = enforcement_active(session)
    member_ids: dict[str, UUID] = {}
    if enforced and admins:
        members_stmt = (
            select(WorkspaceMember.id, func.lower(WorkspaceMember.email))
            .where(func.lower(WorkspaceMember.email).in_([email for _, email in admins]))
            .order_by(WorkspaceMember.id)
        )
        if lock:
            members_stmt = members_stmt.with_for_update()
        member_ids = {email: member_id for member_id, email in session.execute(members_stmt).all()}
    remaining = 0
    for user_id, email in admins:
        if user_id == exclude_user_id:
            continue
        if not enforced:
            remaining += 1
            continue
        member_id = member_ids.get(email)
        if member_id is None or member_id == exclude_member_id:
            continue
        remaining += 1
    if remaining == 0:
        raise RoleChangeRejectedError(
            "cannot remove the last workspace admin — promote another user to "
            "admin first. (Admins granted only by WORKSPACE_ADMIN_EMAILS do not "
            "count: that allowlist is a recovery path, not the invariant.)",
            detail={"stored_admin_count": len(admins)},
        )
