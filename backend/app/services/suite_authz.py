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

from backend.app.core.errors import DataQError
from backend.app.core.roles import DEFAULT_WORKSPACE_ROLE, resolve_role
from backend.app.db.models import Share, Suite, User
from backend.app.services.suite_service import SuiteNotFoundError

OWNER = "owner"
ADMIN = "admin"
VIEW = "view"
#: The workspace-role name (`users.role`), not a suite level — the two
#: vocabularies are separate ladders that happen to share the word "admin".
#: Named here so the Viewer cap below reads as the role check it is.
VIEWER = "viewer"

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
    return _workspace_role(session, user_id) == ADMIN


def _workspace_role(session: Session, user_id: uuid.UUID) -> str:
    """The user's effective workspace role, or `member` if the row is gone.

    A missing row falls back to `member` — the neutral tier — because that is
    what the pre-#741 code did implicitly (`user is not None and …` → not admin)
    and because neither of the two things a role decides here should fire on a
    ghost: it must not confer admin, and it must not apply the Viewer cap to
    somebody we cannot see. In practice a deleted user has no shares and owns
    nothing, so the level resolves to `None` on its own.
    """
    user = session.get(User, user_id)
    return resolve_role(user) if user is not None else DEFAULT_WORKSPACE_ROLE


def _cap_for_viewer(level: str | None, role: str) -> str | None:
    """Clamp a resolved suite level to `view` for a workspace **Viewer**.

    The second of ADR 0033 decision 5's two belts (the first is
    `share_service._reject_edit_share_to_viewer`, which stops the grant being
    made at all). This one is the *enforcement*, and it is not redundant with the
    grant check — it covers the two cases a grant-time check structurally cannot:

    * **Legacy rows.** An `edit` share granted before this shipped is already in
      the table; nothing revalidates it.
    * **Demotion after the grant.** A Member holding `edit` who is later demoted
      to Viewer keeps a row that says `edit`. Roles resolve per request precisely
      so that a demotion takes effect immediately (ADR 0033 decision 7) — a
      demotion that left stale `edit` shares live would make that guarantee
      false exactly where it matters most.

    `owner` is capped too, and deliberately: a Viewer who created a suite before
    being demoted is still read-only, which is what the tier *means*. They keep
    `view` (so the suite stays visible rather than vanishing — existence-hiding
    would be a worse surprise than losing the buttons); managing or deleting it
    falls to a workspace admin, who is implicit `admin` on every suite.
    """
    if level is None or role != VIEWER:
        return level
    return VIEW


class SuiteForbiddenError(DataQError):
    status_code = 403
    code = "suite_forbidden"


def effective_permission(session: Session, suite: Suite, user_id: uuid.UUID) -> str | None:
    """The user's level on `suite` (`owner`/`admin`/`edit`/`view`), or None.

    Ranking: creator → `owner`; workspace-admin → `admin` (implicit, every suite;
    ranks above any share they might also hold); else their `shares` row; else
    None — then clamped to `view` if they are a workspace Viewer (ADR 0033).
    """
    role = _workspace_role(session, user_id)
    if role == ADMIN:
        # Checked before ownership only because the two coincide harmlessly
        # (owner outranks admin), and putting it first keeps the one role lookup
        # doing double duty for the cap below.
        return OWNER if suite.created_by == user_id else ADMIN
    if suite.created_by == user_id:
        return _cap_for_viewer(OWNER, role)
    share = session.scalars(
        select(Share).where(Share.suite_id == suite.id, Share.user_id == user_id)
    ).first()
    return _cap_for_viewer(share.permission if share is not None else None, role)


def effective_permissions(
    session: Session, suites: Sequence[Suite], user_id: uuid.UUID
) -> dict[uuid.UUID, str | None]:
    """Batch `effective_permission` for many suites in one shares query (no N+1).

    Owned suites resolve to `owner` without touching `shares`; the rest are
    looked up in a single `IN` query. Used to stamp each suite in a list with the
    caller's level so the UI can gate per-suite actions (manage shares, delete).

    A workspace-admin is an implicit `admin` on every suite they don't own
    (ADR 0027), resolved once here — no per-suite shares lookup needed.

    Applies the Viewer cap identically to `effective_permission`. It has to: this
    is what stamps each row of the suites LIST, and a list that offered Edit and
    Delete on suites whose detail view then 403s would be a worse failure than no
    cap at all — the user would be told they can do something the server has
    already decided they cannot. `test_batch_and_single_agree_for_every_role`
    pins the two together rather than trusting that they were kept in step.
    """
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
        s.id: _cap_for_viewer(OWNER if s.id in owned else levels.get(s.id), role) for s in suites
    }


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
