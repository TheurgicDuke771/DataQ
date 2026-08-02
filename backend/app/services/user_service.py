"""User directory lookup — search the single-tenant user table.

The sharing UI grants access by `user_id`, but a human only knows an email or
name; this is the search that turns one into the other. Single tenant, so any
authenticated user may search the whole directory — there is no per-tenant
scoping to apply. FastAPI-free: takes a `Session` + query string.
"""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.app.db.models import User

# A short query would match most of the directory; require enough to be a real
# prefix/substring before we run the scan.
MIN_QUERY_LEN = 2
# Cap the result set so a broad term can't return the whole directory in one go.
MAX_LIMIT = 50
DEFAULT_LIMIT = 20


def _escape_like(term: str) -> str:
    r"""Escape LIKE wildcards so a user's literal `%` / `_` / `\` stays literal."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def update_display_name(session: Session, user: User, display_name: str) -> User:
    """Set `user.display_name` and mark it self-service-set (#1139).

    The caller (the `/me` PATCH handler) owns validation — non-empty after
    strip, `<=256` chars to match the column — so this is a plain assign +
    commit, not a re-validation. Self-service only: there is no `user_id`
    parameter, so this can never be pointed at anyone but the row the caller
    already resolved via `get_current_user`.

    Also flips `display_name_override` to `True` (migration 6230293aea96) —
    the marker `_upsert_user`/`_claim_unlinked_user` (core/auth.py) check
    before syncing an AAD token's `name` claim over this value. Every call
    here sets it True, with no path back to False: `MeUpdate.display_name`
    (api/v1/me.py) rejects empty/whitespace-only input, so PATCH /me has no
    "clear the name" operation today — there is nothing to reset the flag
    FOR. If a future change adds one (e.g. an explicit `display_name: null`),
    it should reset `display_name_override` to `False` alongside the clear,
    so the very next AAD login re-seeds a name instead of leaving both
    NULL and "overridden" — the one combination this module never produces
    on purpose.
    """
    user.display_name = display_name
    user.display_name_override = True
    session.commit()
    session.refresh(user)
    return user


def search_users(session: Session, query: str, *, limit: int = DEFAULT_LIMIT) -> list[User]:
    """Find users whose email or display name contains `query` (case-insensitive).

    Returns `[]` for a query shorter than `MIN_QUERY_LEN` (the caller's
    type-ahead simply shows nothing until enough is typed). `limit` is clamped
    to `[1, MAX_LIMIT]`. Results are ordered by email for a stable list.
    """
    term = query.strip()
    if len(term) < MIN_QUERY_LEN:
        return []
    capped = max(1, min(limit, MAX_LIMIT))
    pattern = f"%{_escape_like(term)}%"
    stmt = (
        select(User)
        .where(
            or_(
                User.email.ilike(pattern, escape="\\"),
                func.coalesce(User.display_name, "").ilike(pattern, escape="\\"),
            )
        )
        .order_by(User.email)
        .limit(capped)
    )
    return list(session.scalars(stmt))
