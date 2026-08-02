"""Email one-time-code sign-in — ADR 0032 decisions 4, 5 and 6 (#734, #735, #1127).

A 6-digit code carries about **20 bits** of entropy. That single fact drives every
design choice here: the protection is the *caps*, not the hash.

* 10-minute TTL, single use, at most :data:`MAX_ATTEMPTS` guesses per code.
* A re-request supersedes every outstanding code for the address, so an attacker
  cannot bank a pile of live codes to guess against in parallel.
* The comparison is `hmac.compare_digest` over UTF-8 **bytes** — the digits are
  short enough that a timing side-channel is worth denying, and encoding first is
  mandatory because `compare_digest` raises `TypeError` on non-ASCII `str`
  (the `api/v1/orchestration.py` precedent; a hostile code must be a 401, not a
  500).
* SHA-256 at rest is defence-in-depth against a database read, not a work factor.

**Anti-enumeration** (decision 4): `request_code` returns the same outcome shape
whether the address is eligible, ineligible, or throttled — and sends mail only for
the eligible case, so nothing a caller can observe *in the response body or status*
distinguishes "you have an account here" from "you do not". This is content-level,
not timing-level: the eligible path does Redis + two DB writes + a synchronous mail
send that the ineligible path skips, so response *latency* still leaks membership.
That is a known, tracked gap (a constant-time floor is
[#1137](https://github.com/TheurgicDuke771/DataQ/issues/1137)), not a property this
module claims to close.

**Identity linking** (decision 6, #735 step 2): a successful verification resolves
the user by unique `lower(email)`. If a row already exists — AAD-provisioned or
not — that row *is* the user. Never two rows for one human, or suite grants,
shares and PATs fragment across authenticators.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.db.models import OtpCode, User

log = get_logger(__name__)

CODE_DIGITS = 6
CODE_TTL_MINUTES = 10
#: Verification attempts allowed per code before it is dead (ADR 0032 decision 4).
#: With 10^6 codes and a 10-minute TTL this bounds an online guess at 5e-6 per
#: minted code — the whole security argument for a 6-digit secret.
MAX_ATTEMPTS = 5
#: The per-email request counter's window. Fixed at 10 minutes to match the code
#: TTL: the quantity being bounded is "live codes an attacker can cause to be
#: mailed to one mailbox", and that is exactly a TTL's worth.
EMAIL_WINDOW_SECONDS = 600


class CodeMailer(Protocol):
    """What this service needs from a mailer — one method, nothing else.

    A Protocol rather than the concrete `OtpMailer` so the dependency is the
    *capability*, and so a test can substitute the transport without substituting
    (or subclassing) the thing under test. `otp_mailer.OtpMailer` satisfies it
    structurally; its own error types are what propagate to the caller.
    """

    def send_code(self, *, to: str, code: str, expires_in_minutes: int) -> None: ...


def normalize_email(email: str) -> str:
    """The ONE email normalization rule: strip + lower.

    Shared verbatim with `Settings.is_admin_email` (`core/config.py`) and with the
    `uq_users_email_lower` index (`7d25617cfaf0`) — the index can only express the
    `lower` half, so this function is where `strip` lives. Anything that keys on an
    address goes through here; a second, subtly different rule anywhere on the
    identity surface would silently split one human into two accounts.
    """
    return email.strip().lower()


def is_signup_eligible(email: str, settings: Settings | None = None) -> bool:
    """Whether `email` (already normalized) may sign up / sign in via OTP.

    Mandatory gating, no open registration (ADR 0032 decision 5): DataQ holds
    failing-row samples, which are PII, so self-provisioning by anyone who can
    receive mail is not an acceptable default. An empty allowlist means *nobody*
    is eligible — and the startup validator refuses to boot in that state rather
    than let the uniform response hide it.
    """
    s = settings or get_settings()
    if email in s.auth_otp_allowed_email_set:
        return True
    _, _, domain = email.partition("@")
    return bool(domain) and domain in s.auth_otp_allowed_domain_set


class OtpVerifyError(DataQError):
    """The code is wrong, expired, already used, or out of attempts — always 401.

    One type, one message, for every failure mode — the `ApiKeyAuthError` /
    `SessionAuthError` discipline. Distinguishing "wrong code" from "no code was
    ever requested for this address" would turn the verify endpoint into the
    enumeration oracle the request endpoint was carefully built not to be.
    """

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


# ── Per-email request counters (#1127, service half) ─────────────────────────
#
# Why this is NOT `core.rate_limit.RateLimitStore`, though it is the same idea:
# that Protocol is `async` and its Redis client is bound to the event loop the
# middleware runs on. These endpoints are deliberately SYNCHRONOUS (`def`, so
# Starlette runs them in a threadpool) because `otp/request` performs a blocking
# SMTP submission — putting a five-second network call on the event loop would
# stall every other request in the process. A sync handler cannot await an async
# store, and driving one with `asyncio.run` per request would build and discard an
# event loop that the cached async Redis connections are bound to. So: the same
# fixed-window algorithm, the same key convention, a sync client with the same
# bounded timeouts as `worker/beat_watchdog.build_store`.


class OtpCounterStore(Protocol):
    """One fixed-window counter increment. `None` = store unavailable → fail open."""

    def incr_window(self, key: str, ttl_seconds: int) -> int | None: ...


class InMemoryOtpCounterStore:
    """Process-local counter — for tests, never a production fallback.

    A per-process fallback would silently fragment the cap across replicas, which
    is worse than the documented fail-open: it would look like enforcement.
    """

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def incr_window(self, key: str, ttl_seconds: int) -> int | None:
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]


class RedisOtpCounterStore:
    """INCR + EXPIRE in one pipeline, with bounded socket timeouts.

    Unbounded timeouts are the `#854` failure mode: `redis.from_url` defaults both
    to `None`, i.e. block forever — on the sign-in path that would hang a request
    thread rather than fail open.
    """

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._client: object | None = None

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
        try:
            pipe = self._get_client().pipeline()  # type: ignore[attr-defined]
            pipe.incr(key)
            pipe.expire(key, ttl_seconds)
            count, _ = pipe.execute()
            return int(count)
        except Exception:
            # Fail OPEN, like the middleware (ADR 0035's deliberate bias:
            # availability over enforcement). A Redis outage must not lock every
            # user out of signing in.
            return None


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
    """`otp:req:<sha256(email)>:<window>`.

    The address is **hashed** into the key, never stored in plaintext: Redis keys
    are visible to anyone with `SCAN`, and a workspace's member list is exactly
    what the uniform response exists to hide. The window index rides in the key so
    there is no read-modify-EXPIRE race (the `core.rate_limit` design).
    """
    digest = hashlib.sha256(email.encode()).hexdigest()[:32]
    window = int(now) // EMAIL_WINDOW_SECONDS
    return f"otp:req:{digest}:{window}"


def _within_email_quota(email: str, settings: Settings) -> bool:
    """False when this address has already spent its window's requests.

    ACTIVE regardless of `RATE_LIMIT_ENABLED` — that flag governs the HTTP
    middleware, which dev and E2E turn off; a mail-bomb control a test harness can
    switch off is not a control (#1127).
    """
    global _counter_unavailable_warned
    limit = settings.auth_otp_request_per_email_per_10min
    if limit <= 0:
        return True
    import time

    count = get_counter_store().incr_window(
        _email_bucket_key(email, now=time.time()), EMAIL_WINDOW_SECONDS * 2
    )
    if count is None:
        if not _counter_unavailable_warned:
            _counter_unavailable_warned = True
            # NO email, and no key (the key contains the address's digest, which is
            # a stable per-person identifier even though it is not readable). The
            # logger redacts an `email` KEY (`_PII_KEYS`) but this line must not
            # rely on that: it carries neither.
            log.warning("otp_email_counter_store_unavailable", window_seconds=EMAIL_WINDOW_SECONDS)
        return True  # fail open
    return count <= limit


# ── Code lifecycle ───────────────────────────────────────────────────────────


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _generate_code() -> str:
    """A uniformly random `CODE_DIGITS`-digit string, leading zeros preserved.

    `secrets.randbelow(10**n)` — not `randint`/`choice` loops — so the whole space
    including `000000` is reachable with equal probability. A generator that never
    emits a leading zero silently sheds ~10% of an already-small keyspace.
    """
    return f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"


@dataclass(frozen=True)
class RequestOutcome:
    """What actually happened inside `request_code`.

    Deliberately NOT part of the HTTP response — the endpoint answers identically
    in every case (ADR 0032 decision 4). This exists so the endpoint can log the
    truth, and so tests can assert on the branch taken rather than inferring it
    from a response that is designed to be uninformative.
    """

    sent: bool
    reason: str  # "sent" | "ineligible" | "throttled"


def request_code(
    db: Session,
    email: str,
    *,
    mailer: CodeMailer,
    settings: Settings | None = None,
) -> RequestOutcome:
    """Mint and mail a code for `email`, if it is eligible and under quota.

    Returns what happened; the CALLER must not vary its response on it. Raises only
    for a genuine operator/transport failure on an eligible address (the mailer's
    502/503 classes) — see the endpoint for why that residual asymmetry is
    accepted.
    """
    s = settings or get_settings()
    normalized = normalize_email(email)

    if not is_signup_eligible(normalized, s):
        # Send NOTHING. Not a "rejected" mail, not a log line naming the address —
        # a mail here would also make DataQ a mail-bomb amplifier for arbitrary
        # third-party addresses.
        log.info("otp_request_ineligible")
        return RequestOutcome(sent=False, reason="ineligible")

    if not _within_email_quota(normalized, s):
        log.warning("otp_request_throttled", limit=s.auth_otp_request_per_email_per_10min)
        return RequestOutcome(sent=False, reason="throttled")

    now = datetime.now(UTC)
    # Supersede every outstanding code for this address BEFORE minting the new one,
    # so at no instant are two codes live: otherwise a re-request would hand an
    # attacker a second parallel guessing budget, and MAX_ATTEMPTS would bound
    # nothing (ADR 0032 decision 4).
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

    # Committed BEFORE the send: if the SMTP call fails the user gets a real error
    # and retries, and the stored code simply expires unused. The reverse order
    # would mail a code that no row backs — the one failure the user cannot
    # recover from, because the code in their inbox would never verify.
    mailer.send_code(to=normalized, code=code, expires_in_minutes=CODE_TTL_MINUTES)
    log.info("otp_request_sent", expires_in_minutes=CODE_TTL_MINUTES)
    return RequestOutcome(sent=True, reason="sent")


def verify_code(db: Session, email: str, code: str, *, settings: Settings | None = None) -> User:
    """Verify `code` for `email` → the signed-in `User`, or raise `OtpVerifyError`.

    On success the code is consumed atomically and the user is resolved by
    normalized email (creating the row only if none exists).
    """
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
    #
    # The obvious `row.attempts += 1; db.commit()` reads, increments in Python and
    # writes back — so two guesses arriving together both read `attempts = 4`, both
    # write 5, and the attacker spends ONE attempt on TWO guesses. Repeated with
    # enough concurrency the cap stops bounding anything, which is the entire
    # security argument for a 6-digit secret.
    #
    # A single `UPDATE … SET attempts = attempts + 1 … RETURNING` does the read and
    # the write inside one statement under the row lock, so concurrent guesses are
    # serialized and each is charged. The `consumed_at IS NULL` predicate in the
    # same statement is what makes single-use race-proof too: exactly one of two
    # concurrent redemptions of the same code can match it.
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
    # Constant-time over BYTES: `hmac.compare_digest` raises TypeError on a
    # non-ASCII `str`, and the code is caller-supplied, so a unicode payload must
    # not reach it as text (else a 500 where a 401 belongs — the exact trap
    # `api/v1/orchestration.py` documents on the webhook signatures).
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
    """The identity linking rule — ADR 0032 decision 6 / #735 step 2.

    **One user row per normalized email.** If a row already holds this address it
    IS the user, whether it was provisioned by Azure AD or by an earlier OTP
    sign-in: mailbox proof is the credential, and in a single-tenant AAD the email
    claim is tenant-controlled, so the join is trustworthy. Anything else would
    fragment suite grants, shares and PATs across two rows for one human.

    Deliberately NOT `core.auth._upsert_user`: that one conflicts on
    `aad_object_id`, which an OTP user does not have. Its `IdentityConflictError`
    path stays exactly as #1131 shipped it, for the AAD direction.
    """
    now = datetime.now(UTC)
    user = db.execute(
        select(User).where(func.lower(User.email) == normalized_email)
    ).scalar_one_or_none()
    if user is not None:
        user.last_seen_at = now
        db.commit()
        return user
    user = User(id=uuid.uuid4(), aad_object_id=None, email=normalized_email, last_seen_at=now)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # Two first-ever sign-ins for one address racing. `uq_users_email_lower`
        # rejects the loser; re-reading gives it the winner's row, which is the
        # right answer — the rule is one row per email, not "my INSERT wins".
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
    """Delete spent/expired code rows older than `older_than_hours`. Returns the count.

    Hygiene, not security (the caps are the security): an `otp_codes` row is a hash
    plus an address, and keeping a permanent log of who tried to sign in and when
    is a PII retention liability with no operational value — the same reasoning as
    the W5 sample-failure sweep.

    ``older_than_hours <= 0`` no-ops (returns 0 without touching the DB) — the same
    "clean off-switch, never an unconditional wipe" contract every sibling sweep
    enforces (`purge_expired_sample_failures` / `sweep_orphan_assets` /
    `sweep_orphan_secrets`, all `<retention> <= 0` → return 0). Load-bearing here,
    not just defensive: the cutoff below is `now - older_than_hours`, so a
    non-positive value collapses it to "now" — every row would match
    `created_at < cutoff`, including codes minted a moment ago, not merely ones
    instantly expiring.
    """
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
