"""Browser sign-in sessions for email OTP — ADR 0032 decision 3 (#734).

A session token is a high-entropy random string (`dq_sess_` + 43 url-safe chars,
~256 bits) minted after a successful OTP verification and delivered as an
HttpOnly cookie, so the SPA never holds it in JS-readable storage. Only its
SHA-256 hex digest is stored.

**Why SHA-256 and an indexed lookup, not argon2 and a constant-time compare.**
ADR 0032 decision 3 says "copy the PAT mechanism", and the PAT rationale (ADR
0026, restated at the top of `api_key_service`) applies verbatim: this is a
machine-generated random secret, not a human password, so there is nothing to
brute-force and a KDF buys nothing — while costing a stretch on *every
authenticated request*. The lookup is O(1) on `uq_sessions_token_hash`, and
looking a digest up by index is not a timing oracle over the secret: the digest
is what is compared, and the attacker cannot walk it back. (The OTP *code* is the
opposite case — 20 bits, guessable — and does get `hmac.compare_digest`; see
`otp_service`.)

**No refresh pair** (ADR 0032 decision 3): `expires_at` is a fixed horizon
(`AUTH_SESSION_TTL_HOURS`, default 24) and re-running the OTP flow is the
refresh. Expiry AND revocation are re-checked on **every** resolve — the columns
are not the invalidation, the seam check is.
"""

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

# A public discriminator, not a credential (S105/B105): every session token starts
# with it so the auth seam can branch session-vs-PAT-vs-JWT by prefix, and so the
# log redactor can recognise a leaked one (`core/logging._BEARER_TOKEN_RE`).
TOKEN_PREFIX = "dq_sess_"  # noqa: S105  # nosec B105
#: The cookie the token rides in. Lives here, beside the token it carries, so the
#: auth seam and the endpoints that set/clear it cannot drift on the name.
COOKIE_NAME = "dataq_session"
# Enough of the token to identify it in a log line without revealing it. The same
# `prefix + 4` shape as `api_key_service._DISPLAY_PREFIX_LEN`.
_DISPLAY_PREFIX_LEN = len(TOKEN_PREFIX) + 4


class SessionAuthError(DataQError):
    """The presented session is unknown, revoked, expired, or orphaned — always 401.

    ONE exception type and ONE message for every failure mode, exactly like
    `ApiKeyAuthError`: telling a caller that a session *exists* but has expired
    confirms the cookie was once real, and telling them it was revoked confirms
    somebody logged out. Neither is information an unauthenticated caller is owed.
    """

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
    """Mint a session for `user`. Returns (row, plaintext token).

    The plaintext exists server-side exactly once, here, on its way into the
    `Set-Cookie` header; it is never stored and never logged.
    """
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
    """Authenticate a presented session token → its owning `User`, or raise.

    Expiry and revocation are checked HERE, on every request — ADR 0032 decision 3
    makes that the testable obligation, because a stored `revoked_at` that no code
    path reads is not a logout.
    """
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

    Never raises for an unknown/expired token: logout is not an authentication
    decision, and a caller holding a dead cookie asking to be logged out has
    already got what they wanted.
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
