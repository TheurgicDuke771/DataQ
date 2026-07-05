"""DataQ-issued personal access tokens (PATs) — ADR 0026 phase 1 (#461).

A PAT is a high-entropy random token (`dq_live_` + 43 url-safe chars,
~256 bits) shown ONCE at creation. Only its SHA-256 hex digest is stored —
sufficient hashing for a random machine secret (unlike a human password there
is nothing to brute-force), and it keeps per-request verification an O(1)
indexed lookup instead of an argon2/bcrypt stretch per call (GitHub/GitLab use
the same scheme for their PATs; rationale recorded in ADR 0026).

The token authenticates as its owning user through the same `get_current_user`
seam as Azure AD — REST and `/mcp` identically — inheriting the owner's
per-suite grants. Keys cascade-delete with the user (lifecycle tied to the
account). The plaintext is never logged; log lines carry `key_prefix` only.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.db.models import ApiKey, User

log = get_logger(__name__)

# A public discriminator, not a credential (S105/B105): every token starts with
# it so the auth seam can branch PAT-vs-JWT by prefix, GitHub-token style.
TOKEN_PREFIX = "dq_live_"  # noqa: S105  # nosec B105
# Shown in lists/logs to identify a key without revealing it: `dq_live_` + 4.
_DISPLAY_PREFIX_LEN = len(TOKEN_PREFIX) + 4

DEFAULT_EXPIRY_DAYS = 90
MAX_EXPIRY_DAYS = 365

# last_used_at is telemetry, not an audit ledger — throttle writes so a chatty
# client doesn't turn every request into an UPDATE.
_LAST_USED_WRITE_INTERVAL = timedelta(seconds=60)


class ApiKeyAuthError(DataQError):
    """The presented token is unknown, revoked, or expired — always a 401.

    One message for every failure mode: distinguishing 'unknown' from
    'revoked'/'expired' would confirm to a probing caller that a credential
    exists.
    """

    def __init__(self) -> None:
        super().__init__(
            code="invalid_api_key",
            message="API key is invalid, revoked, or expired.",
            status_code=401,
        )


class ApiKeyNotFoundError(DataQError):
    def __init__(self, key_id: uuid.UUID) -> None:
        super().__init__(
            code="api_key_not_found",
            message="API key not found.",
            status_code=404,
            detail={"api_key_id": str(key_id)},
        )


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_key(
    db: Session,
    user: User,
    *,
    name: str,
    expires_in_days: int = DEFAULT_EXPIRY_DAYS,
) -> tuple[ApiKey, str]:
    """Mint a PAT for `user`. Returns (row, plaintext) — the ONLY time the
    plaintext exists server-side; it is never stored or logged."""
    if not 1 <= expires_in_days <= MAX_EXPIRY_DAYS:
        raise DataQError(
            code="api_key_expiry_invalid",
            message=f"expires_in_days must be between 1 and {MAX_EXPIRY_DAYS}.",
            status_code=422,
        )
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    key = ApiKey(
        id=uuid.uuid4(),
        user_id=user.id,
        name=name,
        key_prefix=token[:_DISPLAY_PREFIX_LEN],
        key_hash=_hash(token),
        expires_at=datetime.now(UTC) + timedelta(days=expires_in_days),
    )
    db.add(key)
    db.commit()
    db.refresh(key)
    log.info(
        "api_key_created",
        api_key_id=str(key.id),
        user_id=str(user.id),
        key_prefix=key.key_prefix,
        expires_at=key.expires_at.isoformat(),
    )
    return key, token


def list_keys(db: Session, user: User) -> list[ApiKey]:
    """The user's keys, newest first — metadata only (hashes never leave the DB layer)."""
    return list(
        db.execute(
            select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at.desc())
        )
        .scalars()
        .all()
    )


def revoke_key(db: Session, user: User, key_id: uuid.UUID) -> ApiKey:
    """Revoke one of the user's own keys. Idempotent; 404 for another user's key
    (indistinguishable from nonexistent — no cross-user probing)."""
    key = db.execute(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user.id)
    ).scalar_one_or_none()
    if key is None:
        raise ApiKeyNotFoundError(key_id)
    if key.revoked_at is None:
        key.revoked_at = datetime.now(UTC)
        db.commit()
        log.info(
            "api_key_revoked",
            api_key_id=str(key.id),
            user_id=str(user.id),
            key_prefix=key.key_prefix,
        )
    return key


def resolve_token(db: Session, token: str) -> User:
    """Authenticate a presented PAT → its owning User, or raise ApiKeyAuthError.

    O(1): unique-index lookup on the token's hash. Expiry/revocation checked on
    the row; `last_used_at` refreshed at most once per interval.
    """
    key = db.execute(select(ApiKey).where(ApiKey.key_hash == _hash(token))).scalar_one_or_none()
    now = datetime.now(UTC)
    if key is None or key.revoked_at is not None or key.expires_at <= now:
        # Prefix-only logging — never the token.
        log.warning("api_key_auth_failed", key_prefix=token[:_DISPLAY_PREFIX_LEN])
        raise ApiKeyAuthError()
    user = db.get(User, key.user_id)
    if user is None:  # cascade should make this unreachable; fail closed anyway
        log.warning("api_key_orphaned", api_key_id=str(key.id), key_prefix=key.key_prefix)
        raise ApiKeyAuthError()
    if key.last_used_at is None or now - key.last_used_at >= _LAST_USED_WRITE_INTERVAL:
        key.last_used_at = now
        db.commit()
    log.info(
        "auth_user_resolved",
        mode="api_key",
        api_key_id=str(key.id),
        user_id=str(user.id),
    )
    return user
