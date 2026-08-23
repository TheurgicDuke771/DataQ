"""User directory lookup — search the single-tenant user table."""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.app.db.models import User
from backend.app.services import audit_service

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
    """Set `user.display_name` and mark it self-service-set (#1139)."""
    audit_before = audit_service.snapshot("user", user)
    user.display_name = display_name
    user.display_name_override = True
    audit_service.record_entity_change(
        session,
        action="user.profile_update",
        entity_type="user",
        entity=user,
        actor=user,
        before=audit_before,
    )
    session.commit()
    session.refresh(user)
    return user


def search_users(session: Session, query: str, *, limit: int = DEFAULT_LIMIT) -> list[User]:
    """Find users whose email or display name contains `query` (case-insensitive)."""
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
