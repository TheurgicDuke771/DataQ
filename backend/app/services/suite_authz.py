"""Suite authorization — the single primitive every suite-scoped endpoint gates on.

A user's effective permission on a suite is the highest of: **owner** (they are
`suite.created_by` — implicit, immutable, never a share row), **admin** (they are
a **workspace-admin** — implicit on *every* suite, never a share row; ADR 0027),
or their `shares` row (`view` < `edit`). Capability ladder:

    view   — read the suite, its checks, its results
    edit   — + create/update/delete checks, update the suite, trigger runs
    admin  — + manage shares (grant/revoke) AND delete the suite. Held by the
             workspace-admin(s) — `users.role = 'admin'`, or the
             `WORKSPACE_ADMIN_EMAILS` break-glass allowlist (ADR 0033 moved the
             source; the implicit-on-every-suite rule itself is unchanged) —
             the governance path. **Not grantable to normal users** (a share can
             only be `view`/`edit`; ADR 0027).
    owner  — same capabilities as admin, but it is the creator: cannot be
             revoked or demoted, and granting a share to the owner is rejected.

`admin` ranks below `owner` and above the `view`/`edit` shares, so a
workspace-admin always clears an `admin` gate even on a suite they don't own.
Legacy `shares.permission = 'admin'` rows (pre-#482) still resolve to `admin`
until the downgrade migration runs — backward-compatible.

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

from backend.app.core.auth import is_workspace_admin
from backend.app.core.errors import DataQError
from backend.app.db.models import Share, Suite, User
from backend.app.services.suite_service import SuiteNotFoundError

OWNER = "owner"
ADMIN = "admin"

# Ordered capability ranks. `owner` ranks above `admin` so it always clears an
# admin gate, even though their capabilities are identical — the distinction is
# that owner is the immutable creator, `admin` is the workspace-admin (implicit,
# never a grantable share for normal users).
_RANK = {"view": 1, "edit": 2, ADMIN: 3, OWNER: 4}


def _is_workspace_admin(session: Session, user_id: uuid.UUID) -> bool:
    """True iff `user_id` is a workspace admin — stored `users.role` OR allowlist.

    Resolved here — rather than threaded through every `require_permission` call
    site — so a workspace-admin is an implicit `admin` on every suite (ADR 0027,
    the rule itself unchanged by ADR 0033; only its *source* moved). One PK fetch,
    usually an identity-map hit since the request already loaded the user.

    Delegates to `core.auth.resolve_role` rather than re-deriving the OR, so this
    gate and the REST gate cannot drift — the failure mode ADR 0033 is most
    exposed to is exactly a role that means one thing at the router and another
    at the suite ladder. The pre-#740 short-circuit on an empty allowlist is gone
    with the same reasoning: it is no longer sound, because a stored `admin` must
    resolve in a deployment that sets no `WORKSPACE_ADMIN_EMAILS` at all — which,
    after in-app role management (#742), is the expected steady state.
    """
    user = session.get(User, user_id)
    return user is not None and is_workspace_admin(user)


class SuiteForbiddenError(DataQError):
    status_code = 403
    code = "suite_forbidden"


def effective_permission(session: Session, suite: Suite, user_id: uuid.UUID) -> str | None:
    """The user's level on `suite` (`owner`/`admin`/`edit`/`view`), or None.

    Ranking: creator → `owner`; workspace-admin → `admin` (implicit, every suite;
    ranks above any share they might also hold); else their `shares` row; else None.
    """
    if suite.created_by == user_id:
        return OWNER
    if _is_workspace_admin(session, user_id):
        return ADMIN
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

    A workspace-admin is an implicit `admin` on every suite they don't own
    (ADR 0027), resolved once here — no per-suite shares lookup needed.
    """
    owned = {s.id for s in suites if s.created_by == user_id}
    if _is_workspace_admin(session, user_id):
        return {s.id: (OWNER if s.id in owned else ADMIN) for s in suites}
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
