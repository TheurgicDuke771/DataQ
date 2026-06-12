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
