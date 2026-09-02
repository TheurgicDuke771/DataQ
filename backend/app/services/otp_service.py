"""Email one-time-code sign-in — ADR 0032 decisions 4, 5 and 6 (#734, #735, #1127)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.circuit_breaker import (
    DEFAULT_OPEN_SECONDS,
    DEFAULT_TRIP_AFTER,
    CircuitBreaker,
)
from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.core.roles import bootstrap_role, should_promote_to_admin
from backend.app.db.models import ADMIN_ROLE, OtpCode, User

log = get_logger(__name__)

CODE_DIGITS = 6
CODE_TTL_MINUTES = 10
#: Verification attempts allowed per code before it is dead (ADR 0032 decision 4).
MAX_ATTEMPTS = 5
#: The per-email request counter's window.
EMAIL_WINDOW_SECONDS = 600


class CodeMailer(Protocol):
    """What this service needs from a mailer — one method, nothing else."""

    def send_code(self, *, to: str, code: str, expires_in_minutes: int) -> None: ...


def normalize_email(email: str) -> str:
    """The ONE email normalization rule: strip + lower."""
    return email.strip().lower()


def is_signup_eligible(email: str, settings: Settings | None = None) -> bool:
    """Whether `email` (already normalized) may sign up / sign in via OTP."""
    s = settings or get_settings()
    if email in s.auth_otp_allowed_email_set:
        return True
    _, _, domain = email.partition("@")
    return bool(domain) and domain in s.auth_otp_allowed_domain_set


class OtpVerifyError(DataQError):
    """The code is wrong, expired, already used, or out of attempts — always 401."""

    def __init__(self) -> None:
        super().__init__(
            "That sign-in code is not valid. Request a new one.",
            code="invalid_otp_code",
            status_code=401,
        )


class OtpNotConfiguredError(DataQError):
    """Email OTP sign-in is not enabled on this deployment."""

    def __init__(self) -> None:
        super().__init__(
            "Email sign-in is not enabled on this deployment.",
            code="otp_not_configured",
            status_code=503,
        )


# ── Per-email request counters (#1127, service half) ───────────────────────── Why this is NOT
# `core.rate_limit.RateLimitStore`.


class OtpCounterStore(Protocol):
    """One fixed-window counter increment. `None` = store unavailable → fail open."""

    def incr_window(self, key: str, ttl_seconds: int) -> int | None: ...


# This store's breaker tuning — the shared defaults (#1135), named locally so the contract is
# stated where the store is, and so a test can read it without reaching into
# `core.circuit_breaker`.
_BREAKER_TRIP_AFTER = DEFAULT_TRIP_AFTER
_BREAKER_OPEN_SECONDS = DEFAULT_OPEN_SECONDS


def _breaker_now() -> float:
    """Clock indirection for the counter store's breaker, so a test can shift the
    open window without sleeping. Monotonic: an NTP step backwards would otherwise
    extend an open window arbitrarily.
    """
    return time.monotonic()


class InMemoryOtpCounterStore:
    """Process-local counter — for tests, never a production fallback."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def incr_window(self, key: str, ttl_seconds: int) -> int | None:
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]


class RedisOtpCounterStore:
    """INCR + EXPIRE in one pipeline, with bounded socket timeouts and a breaker."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._client: object | None = None
        self._breaker = CircuitBreaker(
            name="otp_email_counter_store",
            trip_after=_BREAKER_TRIP_AFTER,
            open_seconds=_BREAKER_OPEN_SECONDS,
            clock=lambda: _breaker_now(),
        )

    def _get_client(self) -> object:
        if self._client is None:
            import redis

            self._client = redis.from_url(
                self._redis_url,
                socket_connect_timeout=0.5,
                socket_timeout=0.5,
            )
        return self._client

    def incr_window(self, key: str, ttl_seconds: int) -> int | None:
        if self._breaker.is_open():
            # Fail open WITHOUT calling Redis — the whole point is to stop paying
            # the timeout on every sign-in while Redis is unwell.
            return None
        try:
            pipe = self._get_client().pipeline()  # type: ignore[attr-defined]
            pipe.incr(key)
            pipe.expire(key, ttl_seconds)
            count, _ = pipe.execute()
            counted = int(count)
        except Exception:
            # Fail OPEN, like the middleware (ADR 0035's deliberate bias: availability over
            # enforcement).
            self._breaker.record_failure()
            return None
        self._breaker.record_success()
        return counted


_counter_store: OtpCounterStore | None = None
_counter_unavailable_warned = False


def get_counter_store() -> OtpCounterStore:
    global _counter_store
    if _counter_store is None:
        _counter_store = RedisOtpCounterStore(get_settings().redis_url)
    return _counter_store


def set_counter_store_for_testing(store: OtpCounterStore | None) -> None:
    """Test hook: inject a store (e.g. `InMemoryOtpCounterStore`) or clear it."""
    global _counter_store, _counter_unavailable_warned
    _counter_store = store
    _counter_unavailable_warned = False


def reset_counter_state() -> None:
    """Test hook mirroring `rate_limit.reset_rate_limit_state`."""
    set_counter_store_for_testing(None)


def _email_bucket_key(email: str, *, now: float) -> str:
    """`otp:req:<sha256(email)>:<window>`."""
    digest = hashlib.sha256(email.encode()).hexdigest()[:32]
    window = int(now) // EMAIL_WINDOW_SECONDS
    return f"otp:req:{digest}:{window}"


def _within_email_quota(email: str, settings: Settings) -> bool:
    """False when this address has already spent its window's requests."""
    global _counter_unavailable_warned
    limit = settings.auth_otp_request_per_email_per_10min
    if limit <= 0:
        return True
    count = get_counter_store().incr_window(
        _email_bucket_key(email, now=time.time()), EMAIL_WINDOW_SECONDS * 2
    )
    if count is None:
        if not _counter_unavailable_warned:
            _counter_unavailable_warned = True
            # NO email, and no key (the key contains the address's digest, which is a stable per-
            # person identifier even though it is not readable).
            log.warning("otp_email_counter_store_unavailable", window_seconds=EMAIL_WINDOW_SECONDS)
        return True  # fail open
    return count <= limit


# ── Code lifecycle ───────────────────────────────────────────────────────────


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _generate_code() -> str:
    """A uniformly random `CODE_DIGITS`-digit string, leading zeros preserved."""
    return f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"


class QueuedCodeMailer:
    """The production `CodeMailer`: hands delivery to the worker (#1731) so the
    request path never waits on SMTP — the floor in `api/v1/auth_otp.py` can pad
    an ineligible address UP to a slow relay, but never an eligible one DOWN.
    """

    def send_code(self, *, to: str, code: str, expires_in_minutes: int) -> None:
        from backend.app.services import run_dispatch

        run_dispatch.dispatch_otp_code(to=to, code=code, expires_in_minutes=expires_in_minutes)


@dataclass(frozen=True)
class RequestOutcome:
    """What actually happened inside `request_code`."""

    sent: bool
    reason: str  # "queued" | "ineligible" | "throttled" | "dispatch_failed"


def request_code(
    db: Session,
    email: str,
    *,
    mailer: CodeMailer,
    settings: Settings | None = None,
) -> RequestOutcome:
    """Mint a code for `email` and hand it to `mailer`, if eligible and under quota.

    Delivery outcome never reaches the caller: a mailer failure is logged and
    reported in the outcome only. The endpoint's response must not depend on
    anything past eligibility (#1731).
    """
    s = settings or get_settings()
    normalized = normalize_email(email)

    if not is_signup_eligible(normalized, s):
        # Send NOTHING.
        log.info("otp_request_ineligible")
        return RequestOutcome(sent=False, reason="ineligible")

    if not _within_email_quota(normalized, s):
        log.warning("otp_request_throttled", limit=s.auth_otp_request_per_email_per_10min)
        return RequestOutcome(sent=False, reason="throttled")

    now = datetime.now(UTC)
    # Supersede every outstanding code for this address BEFORE minting the new one.
    db.execute(
        update(OtpCode)
        .where(OtpCode.email == normalized, OtpCode.consumed_at.is_(None))
        .values(consumed_at=now)
    )
    code = _generate_code()
    db.add(
        OtpCode(
            id=uuid.uuid4(),
            email=normalized,
            code_hash=_hash_code(code),
            expires_at=now + timedelta(minutes=CODE_TTL_MINUTES),
        )
    )
    db.commit()

    # Committed BEFORE the hand-off: a code that never gets delivered simply expires unused, and
    # the user's retry supersedes it.
    try:
        mailer.send_code(to=normalized, code=code, expires_in_minutes=CODE_TTL_MINUTES)
    except Exception as exc:
        # No exc_info: a traceback would carry the address and the code in its frames.
        log.error("otp_request_dispatch_failed", error_type=type(exc).__name__)
        return RequestOutcome(sent=False, reason="dispatch_failed")
    log.info("otp_request_queued", expires_in_minutes=CODE_TTL_MINUTES)
    return RequestOutcome(sent=True, reason="queued")


def verify_code(db: Session, email: str, code: str, *, settings: Settings | None = None) -> User:
    """Verify `code` for `email` → the signed-in `User`, or raise `OtpVerifyError`."""
    s = settings or get_settings()
    normalized = normalize_email(email)
    now = datetime.now(UTC)

    row = db.execute(
        select(OtpCode)
        .where(OtpCode.email == normalized, OtpCode.consumed_at.is_(None))
        .order_by(OtpCode.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        log.warning("otp_verify_failed", reason="no_live_code")
        raise OtpVerifyError()

    # ATOMIC attempt accounting, and it must be atomic in exactly this way.
    updated = db.execute(
        update(OtpCode)
        .where(OtpCode.id == row.id, OtpCode.consumed_at.is_(None))
        .values(attempts=OtpCode.attempts + 1)
        .returning(OtpCode.attempts, OtpCode.code_hash, OtpCode.expires_at)
    ).one_or_none()
    db.commit()
    if updated is None:
        # Consumed between the SELECT and the UPDATE — single use held.
        log.warning("otp_verify_failed", reason="already_consumed")
        raise OtpVerifyError()
    attempts, code_hash, expires_at = updated

    if attempts > MAX_ATTEMPTS:
        log.warning("otp_verify_failed", reason="attempts_exhausted", attempts=attempts)
        raise OtpVerifyError()
    if expires_at <= now:
        log.warning("otp_verify_failed", reason="expired")
        raise OtpVerifyError()
    # Constant-time over BYTES: `hmac.compare_digest` raises TypeError on a non-ASCII `str`, and the
    # code is caller-supplied.
    if not hmac.compare_digest(_hash_code(code).encode("utf-8"), code_hash.encode("utf-8")):
        log.warning("otp_verify_failed", reason="mismatch", attempts=attempts)
        raise OtpVerifyError()

    consumed = db.execute(
        update(OtpCode)
        .where(OtpCode.id == row.id, OtpCode.consumed_at.is_(None))
        .values(consumed_at=now)
        .returning(OtpCode.id)
    ).one_or_none()
    db.commit()
    if consumed is None:
        # Another request redeemed this exact code first. Single-use means the
        # SECOND one loses, even though it presented the right digits.
        log.warning("otp_verify_failed", reason="consumed_concurrently")
        raise OtpVerifyError()

    # Re-check eligibility at redemption, not only at request: an operator who
    # removes somebody from the allowlist within the 10-minute TTL means it.
    if not is_signup_eligible(normalized, s):
        log.warning("otp_verify_failed", reason="no_longer_eligible")
        raise OtpVerifyError()

    user = resolve_or_create_user(db, normalized)
    log.info("otp_verify_succeeded", user_id=str(user.id))
    return user


def resolve_or_create_user(db: Session, normalized_email: str) -> User:
    """The identity linking rule — ADR 0032 decision 6 / #735 step 2."""
    now = datetime.now(UTC)
    user = db.execute(
        select(User).where(func.lower(User.email) == normalized_email)
    ).scalar_one_or_none()
    if user is not None:
        user.last_seen_at = now
        # Promote-only allowlist write-through (ADR 0033 decision 6).
        if should_promote_to_admin(normalized_email):
            user.role = ADMIN_ROLE
        db.commit()
        return user
    user = User(
        id=uuid.uuid4(),
        aad_object_id=None,
        email=normalized_email,
        # ADR 0033 decision 8's precedence lives inside `bootstrap_role`, shared with the OIDC/AAD
        # sign-in path: the allowlist write-through WINS over the signup default.
        role=bootstrap_role(normalized_email, default=get_settings().auth_otp_default_role),
        last_seen_at=now,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # Two first-ever sign-ins for one address racing.
        db.rollback()
        existing = db.execute(
            select(User).where(func.lower(User.email) == normalized_email)
        ).scalar_one_or_none()
        if existing is None:  # pragma: no cover — a different constraint; re-raise
            raise
        return existing
    db.refresh(user)
    log.info("otp_user_provisioned", user_id=str(user.id))
    return user


def purge_expired_codes(db: Session, *, older_than_hours: int = 24) -> int:
    """Delete spent/expired code rows older than `older_than_hours`. Returns the count."""
    if older_than_hours <= 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(hours=older_than_hours)
    result = db.execute(delete(OtpCode).where(OtpCode.created_at < cutoff))
    db.commit()
    deleted = int(result.rowcount or 0)  # type: ignore[attr-defined]  # DELETE yields a CursorResult
    if deleted:
        log.info("otp_codes_purged", deleted=deleted, older_than_hours=older_than_hours)
    return deleted


__all__ = [
    "CODE_DIGITS",
    "CODE_TTL_MINUTES",
    "EMAIL_WINDOW_SECONDS",
    "MAX_ATTEMPTS",
    "CodeMailer",
    "InMemoryOtpCounterStore",
    "OtpCounterStore",
    "OtpNotConfiguredError",
    "OtpVerifyError",
    "QueuedCodeMailer",
    "RedisOtpCounterStore",
    "RequestOutcome",
    "is_signup_eligible",
    "normalize_email",
    "purge_expired_codes",
    "request_code",
    "reset_counter_state",
    "resolve_or_create_user",
    "set_counter_store_for_testing",
    "verify_code",
]
