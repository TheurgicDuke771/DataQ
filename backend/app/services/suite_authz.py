"""Suite authorization — the single primitive every suite-scoped endpoint gates on.

A user's effective permission on a suite is the highest of: **owner** (they are
`suite.created_by` — implicit, immutable, never a share row) or their `shares`
row (`view` < `edit` < `admin`). Capability ladder (decided for v1):

    view   — read the suite, its checks, its results
    edit   — + create/update/delete checks, update the suite, trigger runs
    admin  — + manage shares (grant/revoke) AND delete the suite
    owner  — same capabilities as admin, but it is the creator: cannot be
             revoked or demoted, and granting a share to the owner is rejected.

`require_permission` is the gate the API layer calls: it 404s a suite the user
can't see at all (existence is hidden), and 403s one they can see but lack the
level for. It returns the `Suite` so callers don't re-fetch.

FastAPI-free (takes a `Session` + the user id); the API layer passes
`current_user.id`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.db.models import Share, Suite
from backend.app.services.suite_service import SuiteNotFoundError

OWNER = "owner"

# Ordered capability ranks. `owner` ranks above `admin` so it always clears an
# admin gate, even though their capabilities are identical — the distinction is
# that owner is the immutable creator, not a grantable/revocable share.
_RANK = {"view": 1, "edit": 2, "admin": 3, OWNER: 4}


class SuiteForbiddenError(DataQError):
    status_code = 403
    code = "suite_forbidden"


def effective_permission(session: Session, suite: Suite, user_id: uuid.UUID) -> str | None:
    """The user's level on `suite` (`owner`/`admin`/`edit`/`view`), or None."""
    if suite.created_by == user_id:
        return OWNER
    share = session.scalars(
        select(Share).where(Share.suite_id == suite.id, Share.user_id == user_id)
    ).first()
    return share.permission if share is not None else None


def effective_permissions(
    session: Session, suites: Sequence[Suite], user_id: uuid.UUID
) -> dict[uuid.UUID, str | None]:
    """Batch `effective_permission` for many suites in one shares query (no N+1).

    Owned suites resolve to `owner` without touching `shares`; the rest are
    looked up in a single `IN` query. Used to stamp each suite in a list with the
    caller's level so the UI can gate per-suite actions (manage shares, delete).
    """
    owned = {s.id for s in suites if s.created_by == user_id}
    shared_ids = [s.id for s in suites if s.id not in owned]
    levels: dict[uuid.UUID, str] = {}
    if shared_ids:
        rows = session.scalars(
            select(Share).where(Share.user_id == user_id, Share.suite_id.in_(shared_ids))
        )
        levels = {row.suite_id: row.permission for row in rows}
    return {s.id: (OWNER if s.id in owned else levels.get(s.id)) for s in suites}


def require_permission(
    session: Session,
    suite_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    minimum: str,
) -> Suite:
    """Return the suite iff the user has at least `minimum` permission on it.

    Raises `SuiteNotFoundError` (404) if the suite doesn't exist **or** the user
    has no access at all (existence is hidden), and `SuiteForbiddenError` (403)
    if they have some access but below `minimum`.
    """
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
