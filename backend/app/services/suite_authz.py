"""Suite authorization — the single primitive every suite-scoped endpoint gates on."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.roles import DEFAULT_WORKSPACE_ROLE, resolve_role
from backend.app.db.models import Share, Suite, User
from backend.app.services.suite_service import SuiteNotFoundError

OWNER = "owner"
ADMIN = "admin"
VIEW = "view"
#: The workspace-role name (`users.role`), not a suite level — the two vocabularies are separate
#: ladders that happen to share the word "admin".
VIEWER = "viewer"

# Ordered capability ranks.
_RANK = {"view": 1, "edit": 2, ADMIN: 3, OWNER: 4}


def _workspace_role(session: Session, user_id: uuid.UUID) -> str:
    """The user's effective workspace role, or `member` if the row is gone."""
    user = session.get(User, user_id)
    return resolve_role(user) if user is not None else DEFAULT_WORKSPACE_ROLE


def cap_for_viewer(level: str | None, role: str) -> str | None:
    """Clamp a resolved suite level to `view` for a workspace **Viewer**."""
    if level is None or role != VIEWER:
        return level
    return VIEW


class SuiteForbiddenError(DataQError):
    status_code = 403
    code = "suite_forbidden"


def effective_permission(session: Session, suite: Suite, user_id: uuid.UUID) -> str | None:
    """The user's level on `suite` (`owner`/`admin`/`edit`/`view`), or None."""
    role = _workspace_role(session, user_id)
    if role == ADMIN:
        # Checked before ownership only because the two coincide harmlessly (owner outranks admin),
        # and putting it first keeps the one role lookup doing double duty for the cap below.
        return OWNER if suite.created_by == user_id else ADMIN
    if suite.created_by == user_id:
        return cap_for_viewer(OWNER, role)
    share = session.scalars(
        select(Share).where(Share.suite_id == suite.id, Share.user_id == user_id)
    ).first()
    return cap_for_viewer(share.permission if share is not None else None, role)


def effective_permissions(
    session: Session, suites: Sequence[Suite], user_id: uuid.UUID
) -> dict[uuid.UUID, str | None]:
    """Batch `effective_permission` for many suites in one shares query (no N+1)."""
    role = _workspace_role(session, user_id)
    owned = {s.id for s in suites if s.created_by == user_id}
    if role == ADMIN:
        return {s.id: (OWNER if s.id in owned else ADMIN) for s in suites}
    shared_ids = [s.id for s in suites if s.id not in owned]
    levels: dict[uuid.UUID, str] = {}
    if shared_ids:
        rows = session.scalars(
            select(Share).where(Share.user_id == user_id, Share.suite_id.in_(shared_ids))
        )
        levels = {row.suite_id: row.permission for row in rows}
    return {
        s.id: cap_for_viewer(OWNER if s.id in owned else levels.get(s.id), role) for s in suites
    }


def require_permission(
    session: Session,
    suite_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    minimum: str,
) -> Suite:
    """Return the suite iff the user has at least `minimum` permission on it."""
    suite = session.get(Suite, suite_id)
    if suite is None:
        raise SuiteNotFoundError("suite not found", detail={"suite_id": str(suite_id)})
    level = effective_permission(session, suite, user_id)
    if level is None:
        # No access → indistinguishable from "doesn't exist" (don't leak the id).
        raise SuiteNotFoundError("suite not found", detail={"suite_id": str(suite_id)})
    if _RANK[level] < _RANK[minimum]:
        raise SuiteForbiddenError(
            f"this action requires {minimum!r} permission on the suite",
            detail={"suite_id": str(suite_id), "have": level, "need": minimum},
        )
    return suite
