"""Browser sign-in sessions for email OTP — ADR 0032 decision 3 (#734)."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.db.models import User, UserSession

log = get_logger(__name__)

# A public discriminator, not a credential (S105/B105): every session token starts with it so the
# auth seam can branch session-vs-PAT-vs-JWT by prefix.
TOKEN_PREFIX = "dq_sess_"  # noqa: S105  # nosec B105
#: The cookie the token rides in. Lives here, beside the token it carries, so the
#: auth seam and the endpoints that set/clear it cannot drift on the name.
COOKIE_NAME = "dataq_session"
# Enough of the token to identify it in a log line without revealing it. The same
# `prefix + 4` shape as `api_key_service._DISPLAY_PREFIX_LEN`.
_DISPLAY_PREFIX_LEN = len(TOKEN_PREFIX) + 4


class SessionAuthError(DataQError):
    """The presented session is unknown, revoked, expired, or orphaned — always 401."""

    def __init__(self) -> None:
        super().__init__(
            "Session is invalid, expired, or signed out.",
            code="invalid_session",
            status_code=401,
        )


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _display_prefix(token: str) -> str:
    return token[:_DISPLAY_PREFIX_LEN]


def create_session(
    db: Session, user: User, *, settings: Settings | None = None
) -> tuple[UserSession, str]:
    """Mint a session for `user`. Returns (row, plaintext token)."""
    s = settings or get_settings()
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = UserSession(
        id=uuid.uuid4(),
        user_id=user.id,
        token_hash=_hash(token),
        expires_at=datetime.now(UTC) + timedelta(hours=s.auth_session_ttl_hours),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    log.info(
        "session_created",
        session_id=str(row.id),
        user_id=str(user.id),
        expires_at=row.expires_at.isoformat(),
    )
    return row, token


def resolve_token(db: Session, token: str) -> User:
    """Authenticate a presented session token → its owning `User`, or raise."""
    row = db.execute(
        select(UserSession).where(UserSession.token_hash == _hash(token))
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if row is None or row.revoked_at is not None or row.expires_at <= now:
        # Prefix only — never the token, which is a live credential.
        log.warning("session_auth_failed", session_prefix=_display_prefix(token))
        raise SessionAuthError()
    user = db.get(User, row.user_id)
    if user is None:  # the CASCADE should make this unreachable; fail closed anyway
        log.warning("session_orphaned", session_id=str(row.id))
        raise SessionAuthError()
    log.info("auth_user_resolved", mode="session", session_id=str(row.id), user_id=str(user.id))
    return user


def revoke(db: Session, token: str) -> bool:
    """Revoke the session identified by `token`. Idempotent; returns whether this
    call was the one that revoked it.
    """
    row = db.execute(
        select(UserSession).where(UserSession.token_hash == _hash(token))
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return False
    row.revoked_at = datetime.now(UTC)
    db.commit()
    log.info("session_revoked", session_id=str(row.id), user_id=str(row.user_id))
    return True
