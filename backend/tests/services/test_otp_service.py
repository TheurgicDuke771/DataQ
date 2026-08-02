"""The OTP code lifecycle — where a 20-bit secret is made safe by caps (#734).

A 6-digit code has ~20 bits of entropy, so every property asserted here is
load-bearing rather than hygiene: drop the attempt cap, or let a re-request leave
the old code live, or make the "single use" check non-atomic, and the credential
becomes guessable in a way no amount of hashing would fix.

Also covers the two halves that are not about codes at all: the identity linking
rule (#735 step 2 — one user row per normalized email) and the per-email request
counters (#1127).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from backend.app.core.config import Settings
from backend.app.db.models import ApiKey, OtpCode, User
from backend.app.services import api_key_service
from backend.app.services import otp_service as svc


class _RecordingMailer:
    """Stands in for `OtpMailer` — captures what would have been sent.

    Substitutes the SMTP transport, not the service under test. The real mailer
    gets its own boundary tests in `test_otp_mailer.py` (mocking `smtplib`, not
    our own code).
    """

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, int]] = []

    def send_code(self, *, to: str, code: str, expires_in_minutes: int) -> None:
        self.sent.append((to, code, expires_in_minutes))


class _FailingMailer:
    def send_code(self, *, to: str, code: str, expires_in_minutes: int) -> None:
        from backend.app.services.otp_mailer import OtpMailSendError

        raise OtpMailSendError()


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "auth_email_smtp_host": "smtp.example.com",
        "auth_email_username": "dataq@example.com",
        "auth_email_from": "dataq@example.com",
        "auth_email_password_secret_name": "auth-email-password",
        "auth_otp_allowed_domains": "acme.io",
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture(autouse=True)
def _in_memory_counter() -> Any:
    """Per-email counters on an in-process store, reset per test.

    Deliberately ACTIVE (not disabled) in every test here — #1127's whole point is
    that this layer does not depend on `RATE_LIMIT_ENABLED`, and a fixture that
    turned it off would make the tests pass on a build where it never runs.
    """
    svc.set_counter_store_for_testing(svc.InMemoryOtpCounterStore())
    yield
    svc.reset_counter_state()


def _address() -> str:
    return f"user-{uuid.uuid4().hex[:10]}@acme.io"


def _request(db: Any, email: str, settings: Settings | None = None) -> tuple[Any, str]:
    mailer = _RecordingMailer()
    outcome = svc.request_code(db, email, mailer=mailer, settings=settings or _settings())
    return outcome, mailer.sent[-1][1] if mailer.sent else ""


# ── normalization + eligibility ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Ada@Acme.IO ", "ada@acme.io"),
        ("ADA@ACME.IO", "ada@acme.io"),
        ("ada@acme.io", "ada@acme.io"),
    ],
)
def test_normalization_is_strip_plus_lower(raw: str, expected: str) -> None:
    assert svc.normalize_email(raw) == expected


def test_normalization_matches_the_admin_allowlist_rule() -> None:
    """One rule across the identity surface, or one human becomes two accounts."""
    s = Settings(workspace_admin_emails="Ada@Acme.IO")
    assert s.is_admin_email(svc.normalize_email("  ADA@acme.io "))


@pytest.mark.parametrize(
    ("email", "eligible"),
    [
        ("ada@acme.io", True),  # allowed domain
        ("ada@ACME.io", False),  # NOT normalized by the caller → the caller must normalize
        ("grace@other.org", False),
        ("nobody@", False),
        ("no-at-sign", False),
        ("", False),
    ],
)
def test_domain_allowlist(email: str, eligible: bool) -> None:
    assert svc.is_signup_eligible(email, _settings()) is eligible


def test_explicit_email_allowlist_admits_an_off_domain_address() -> None:
    s = _settings(auth_otp_allowed_emails="Grace@Other.ORG", auth_otp_allowed_domains="")
    assert svc.is_signup_eligible("grace@other.org", s)
    assert not svc.is_signup_eligible("ada@other.org", s)


def test_a_domain_suffix_is_not_a_domain_match() -> None:
    """`evil-acme.io` must not pass an `acme.io` allowlist — a substring/`endswith`
    implementation would admit an attacker-registered lookalike domain."""
    assert not svc.is_signup_eligible("ada@evil-acme.io", _settings())
    assert not svc.is_signup_eligible("ada@acme.io.evil.net", _settings())


# ── request: eligibility gating + anti-enumeration ───────────────────────────


def test_an_eligible_address_gets_a_code_and_a_row(db_session: Any) -> None:
    email = _address()
    outcome, code = _request(db_session, email)

    assert outcome.sent is True and outcome.reason == "sent"
    assert len(code) == svc.CODE_DIGITS and code.isdigit()
    rows = db_session.query(OtpCode).filter(OtpCode.email == email).all()
    assert len(rows) == 1
    # Hashed at rest — the plaintext code never lands in the table.
    assert rows[0].code_hash != code


def test_an_INELIGIBLE_address_sends_nothing_and_stores_nothing(db_session: Any) -> None:
    """Not a rejection mail, not a row. Sending anything would also make DataQ a
    mail-bomb amplifier aimed at arbitrary third-party addresses."""
    email = f"stranger-{uuid.uuid4().hex[:8]}@notallowed.example"
    mailer = _RecordingMailer()
    outcome = svc.request_code(db_session, email, mailer=mailer, settings=_settings())

    assert outcome.sent is False and outcome.reason == "ineligible"
    assert mailer.sent == []
    assert db_session.query(OtpCode).filter(OtpCode.email == email).count() == 0


def test_the_address_is_normalized_before_anything_touches_it(db_session: Any) -> None:
    email = _address()
    outcome, _ = _request(db_session, f"  {email.upper()}  ")
    assert outcome.sent is True
    assert db_session.query(OtpCode).filter(OtpCode.email == email).count() == 1


def test_a_send_failure_propagates_rather_than_being_swallowed(db_session: Any) -> None:
    """#734 AC: no quiet no-op. The row is still committed (so a retry is cheap and
    a code already in flight would still verify), but the caller learns."""
    from backend.app.services.otp_mailer import OtpMailSendError

    email = _address()
    with pytest.raises(OtpMailSendError):
        svc.request_code(db_session, email, mailer=_FailingMailer(), settings=_settings())
    assert db_session.query(OtpCode).filter(OtpCode.email == email).count() == 1


# ── verify: the caps ─────────────────────────────────────────────────────────


def test_the_right_code_signs_you_in_exactly_once(db_session: Any) -> None:
    email = _address()
    _, code = _request(db_session, email)

    user = svc.verify_code(db_session, email, code, settings=_settings())
    assert user.email == email

    # Single use: the SAME code, immediately, is dead.
    with pytest.raises(svc.OtpVerifyError):
        svc.verify_code(db_session, email, code, settings=_settings())


def test_a_wrong_code_is_rejected(db_session: Any) -> None:
    email = _address()
    _, code = _request(db_session, email)
    wrong = "000000" if code != "000000" else "111111"
    with pytest.raises(svc.OtpVerifyError):
        svc.verify_code(db_session, email, wrong, settings=_settings())


def test_an_expired_code_is_rejected(db_session: Any) -> None:
    email = _address()
    _, code = _request(db_session, email)
    row = db_session.query(OtpCode).filter(OtpCode.email == email).one()
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    with pytest.raises(svc.OtpVerifyError):
        svc.verify_code(db_session, email, code, settings=_settings())


def test_the_sixth_attempt_is_locked_out_even_with_the_RIGHT_code(db_session: Any) -> None:
    """The attempt cap is the whole security argument for a 6-digit secret.

    Deliberately spends the budget on WRONG guesses and then presents the RIGHT
    one: a cap that only counted failures-after-the-fact, or that reset on a
    correct guess, would still pass a test that only fed it wrong codes.
    """
    email = _address()
    _, code = _request(db_session, email)
    wrong = "000000" if code != "000000" else "111111"

    for _ in range(svc.MAX_ATTEMPTS):
        with pytest.raises(svc.OtpVerifyError):
            svc.verify_code(db_session, email, wrong, settings=_settings())

    with pytest.raises(svc.OtpVerifyError):
        svc.verify_code(db_session, email, code, settings=_settings())
    assert (
        db_session.query(OtpCode).filter(OtpCode.email == email).one().attempts > svc.MAX_ATTEMPTS
    )


def test_attempts_are_charged_before_the_comparison(db_session: Any) -> None:
    """Increment-then-compare, not compare-then-increment.

    The ordering is what makes the cap hold under concurrency: a version that
    incremented only after a failed compare would let two simultaneous guesses
    both read the pre-increment value and spend one attempt between them.
    """
    email = _address()
    _request(db_session, email)
    with pytest.raises(svc.OtpVerifyError):
        svc.verify_code(db_session, email, "999999", settings=_settings())
    assert db_session.query(OtpCode).filter(OtpCode.email == email).one().attempts == 1


def test_a_re_request_kills_the_previous_code(db_session: Any) -> None:
    """Otherwise an attacker banks N live codes and gets N x MAX_ATTEMPTS guesses
    against the same mailbox, and the cap bounds nothing.

    The obvious version of this test — request twice, then try the first code —
    **passes even with the superseding UPDATE deleted**, because `verify_code`
    selects the NEWEST live code, so the older one loses on the hash comparison
    rather than on being dead. Found by mutation-checking it. The assertions below
    are written to kill that mutant:

    * the older ROW must actually carry `consumed_at`, and
    * once the newest code is redeemed, the older one must not become reachable —
      which is exactly what happens without superseding, since the newest-live
      selection then falls back to it. That is the banked-codes attack, arriving
      one redemption later than the naive test looks for.
    """
    email = _address()
    _, first = _request(db_session, email)
    _, second = _request(db_session, email)
    assert first != second

    rows = (
        db_session.query(OtpCode).filter(OtpCode.email == email).order_by(OtpCode.created_at).all()
    )
    assert len(rows) == 2
    assert rows[0].consumed_at is not None, "the superseded code is still live in the table"
    assert rows[1].consumed_at is None

    with pytest.raises(svc.OtpVerifyError):
        svc.verify_code(db_session, email, first, settings=_settings())
    assert svc.verify_code(db_session, email, second, settings=_settings()).email == email

    # …and the superseded code is STILL dead now that the newest one is consumed.
    with pytest.raises(svc.OtpVerifyError):
        svc.verify_code(db_session, email, first, settings=_settings())


def test_verifying_with_no_outstanding_code_is_the_same_401(db_session: Any) -> None:
    with pytest.raises(svc.OtpVerifyError) as never:
        svc.verify_code(db_session, _address(), "123456", settings=_settings())

    email = _address()
    _, code = _request(db_session, email)
    with pytest.raises(svc.OtpVerifyError) as wrong:
        svc.verify_code(db_session, email, "000000" if code != "000000" else "111111")

    assert never.value.message == wrong.value.message
    assert never.value.code == wrong.value.code == "invalid_otp_code"
    assert never.value.status_code == 401


@pytest.mark.parametrize("code", ["ünïcödé", "🔐🔐🔐", "日本語のコード"])
def test_a_non_ascii_code_is_a_401_not_a_500(db_session: Any, code: str) -> None:
    """`hmac.compare_digest` raises TypeError on non-ASCII `str`. Comparing the
    hashes (hex, always ASCII) as UTF-8 BYTES is what keeps a hostile payload a
    401 — the trap `api/v1/orchestration.py` documents on the webhook signatures."""
    email = _address()
    _request(db_session, email)
    with pytest.raises(svc.OtpVerifyError):
        svc.verify_code(db_session, email, code, settings=_settings())


def test_becoming_ineligible_within_the_ttl_invalidates_an_outstanding_code(
    db_session: Any,
) -> None:
    """An operator who removes somebody from the allowlist means it, immediately —
    not "in up to ten minutes"."""
    email = _address()
    _, code = _request(db_session, email)
    with pytest.raises(svc.OtpVerifyError):
        svc.verify_code(
            db_session, email, code, settings=_settings(auth_otp_allowed_domains="x.io")
        )


def test_a_code_for_one_address_cannot_sign_in_another(db_session: Any) -> None:
    victim, attacker = _address(), _address()
    _, victim_code = _request(db_session, victim)
    _request(db_session, attacker)
    with pytest.raises(svc.OtpVerifyError):
        svc.verify_code(db_session, attacker, victim_code, settings=_settings())


def test_generated_codes_are_six_digits_including_leading_zeros() -> None:
    """A generator that skipped `000123` would shed ~10% of an already tiny keyspace."""
    codes = {svc._generate_code() for _ in range(3000)}
    assert all(len(c) == svc.CODE_DIGITS and c.isdigit() for c in codes)
    assert len(codes) > 2000, "the generator is not producing distinct codes"
    assert any(c.startswith("0") for c in codes), "leading zeros are never produced"


# ── per-email counters (#1127) ───────────────────────────────────────────────


def test_the_per_email_cap_stops_the_fourth_request(db_session: Any) -> None:
    email = _address()
    s = _settings(auth_otp_request_per_email_per_10min=3)
    for _ in range(3):
        assert svc.request_code(db_session, email, mailer=_RecordingMailer(), settings=s).sent

    mailer = _RecordingMailer()
    outcome = svc.request_code(db_session, email, mailer=mailer, settings=s)
    assert outcome.sent is False and outcome.reason == "throttled"
    assert mailer.sent == [], "a throttled request still mailed the mailbox"


def test_the_cap_is_PER_ADDRESS_not_global(db_session: Any) -> None:
    s = _settings(auth_otp_request_per_email_per_10min=1)
    first, second = _address(), _address()
    assert svc.request_code(db_session, first, mailer=_RecordingMailer(), settings=s).sent
    assert svc.request_code(db_session, second, mailer=_RecordingMailer(), settings=s).sent
    assert not svc.request_code(db_session, first, mailer=_RecordingMailer(), settings=s).sent


def test_case_variants_of_one_address_share_the_bucket(db_session: Any) -> None:
    """Otherwise `Ada@acme.io`, `ADA@acme.io`, … each mint a fresh budget and the
    cap is trivially bypassed by anyone who can hold down shift."""
    s = _settings(auth_otp_request_per_email_per_10min=1)
    email = _address()
    assert svc.request_code(db_session, email, mailer=_RecordingMailer(), settings=s).sent
    outcome = svc.request_code(db_session, email.upper(), mailer=_RecordingMailer(), settings=s)
    assert outcome.reason == "throttled"


def test_the_cap_is_active_while_RATE_LIMIT_ENABLED_is_false(db_session: Any) -> None:
    """#1127's actual point: the middleware layer is off in dev and E2E, and a
    mail-bomb control a test harness silently disables is not a control."""
    s = _settings(auth_otp_request_per_email_per_10min=1, rate_limit_enabled=False)
    email = _address()
    assert svc.request_code(db_session, email, mailer=_RecordingMailer(), settings=s).sent
    assert not svc.request_code(db_session, email, mailer=_RecordingMailer(), settings=s).sent


def test_the_counter_fails_OPEN_when_the_store_is_down(db_session: Any) -> None:
    """A Redis outage must not lock the whole workspace out of signing in
    (ADR 0035's deliberate bias: availability over enforcement)."""

    class _DownStore:
        def incr_window(self, key: str, ttl_seconds: int) -> int | None:
            return None

    svc.set_counter_store_for_testing(_DownStore())
    s = _settings(auth_otp_request_per_email_per_10min=1)
    email = _address()
    for _ in range(4):
        assert svc.request_code(db_session, email, mailer=_RecordingMailer(), settings=s).sent


def test_the_outage_warning_carries_no_address_in_any_form(db_session: Any) -> None:
    """Not the email, and not the bucket key either — the key holds a stable
    per-person digest, which is a pseudonymous identifier, not an anonymisation."""
    import io
    import logging

    from backend.app.core.logging import configure_logging

    class _DownStore:
        def incr_window(self, key: str, ttl_seconds: int) -> int | None:
            return None

    svc.set_counter_store_for_testing(_DownStore())
    email = _address()
    configure_logging()
    buffer = io.StringIO()
    handler = logging.getLogger().handlers[0]
    original = handler.stream  # type: ignore[attr-defined]
    handler.stream = buffer  # type: ignore[attr-defined]
    try:
        svc.request_code(
            db_session,
            email,
            mailer=_RecordingMailer(),
            settings=_settings(auth_otp_request_per_email_per_10min=1),
        )
    finally:
        handler.stream = original  # type: ignore[attr-defined]

    emitted = buffer.getvalue()
    assert "otp_email_counter_store_unavailable" in emitted
    assert email not in emitted
    assert email.split("@")[0] not in emitted
    import hashlib

    assert hashlib.sha256(email.encode()).hexdigest()[:32] not in emitted


def test_a_zero_limit_disables_the_counter(db_session: Any) -> None:
    s = _settings(auth_otp_request_per_email_per_10min=0)
    email = _address()
    for _ in range(6):
        assert svc.request_code(db_session, email, mailer=_RecordingMailer(), settings=s).sent


def test_the_bucket_key_does_not_contain_the_address(db_session: Any) -> None:
    """Redis keys are readable by anyone with SCAN, and the workspace's member list
    is precisely what the uniform response exists to hide."""
    key = svc._email_bucket_key("ada@acme.io", now=1_700_000_000.0)
    assert "ada" not in key and "acme.io" not in key
    assert key.startswith("otp:req:")


# ── identity linking (#735 step 2, ADR 0032 decision 6) ──────────────────────


def test_an_otp_signin_resolves_to_an_EXISTING_AAD_row_with_pats_intact(db_session: Any) -> None:
    """The rule the whole identity migration exists for: one row per human.

    A second row would silently fork the person's suite grants, shares and PATs —
    they would sign in, see an empty workspace, and nothing would look broken.
    """
    email = _address()
    aad_user = User(id=uuid.uuid4(), aad_object_id=uuid.uuid4().hex, email=email.upper())
    db_session.add(aad_user)
    db_session.commit()
    _, pat = api_key_service.create_key(db_session, aad_user, name="pre-existing")

    _, code = _request(db_session, email)
    resolved = svc.verify_code(db_session, email, code, settings=_settings())

    assert resolved.id == aad_user.id, "OTP sign-in forked a second row for one human"
    assert resolved.aad_object_id == aad_user.aad_object_id, "the AAD identity was clobbered"
    assert db_session.query(User).filter(User.email.ilike(email)).count() == 1
    # The PAT still authenticates as the same user.
    assert api_key_service.resolve_token(db_session, pat).id == aad_user.id
    assert db_session.query(ApiKey).filter(ApiKey.user_id == aad_user.id).count() == 1


def test_a_brand_new_otp_user_is_created_with_a_null_aad_object_id(db_session: Any) -> None:
    email = _address()
    _, code = _request(db_session, email)
    user = svc.verify_code(db_session, email, code, settings=_settings())
    assert user.aad_object_id is None
    assert user.email == email  # stored normalized
    assert user.last_seen_at is not None


def test_signing_in_twice_reuses_the_same_row(db_session: Any) -> None:
    email = _address()
    _, first_code = _request(db_session, email)
    first = svc.verify_code(db_session, email, first_code, settings=_settings())
    _, second_code = _request(db_session, email)
    second = svc.verify_code(db_session, email, second_code, settings=_settings())
    assert first.id == second.id
    assert db_session.query(User).filter(User.email == email).count() == 1


def test_resolve_or_create_matches_case_insensitively(db_session: Any) -> None:
    email = _address()
    db_session.add(User(id=uuid.uuid4(), aad_object_id=None, email=email.upper()))
    db_session.commit()
    assert svc.resolve_or_create_user(db_session, email).email == email.upper()


# ── retention ────────────────────────────────────────────────────────────────


def test_old_code_rows_are_purgeable(db_session: Any) -> None:
    email = _address()
    _request(db_session, email)
    row = db_session.query(OtpCode).filter(OtpCode.email == email).one()
    row.created_at = datetime.now(UTC) - timedelta(days=3)
    db_session.commit()

    assert svc.purge_expired_codes(db_session, older_than_hours=24) >= 1
    assert db_session.query(OtpCode).filter(OtpCode.email == email).count() == 0


def test_purge_leaves_a_live_code_alone(db_session: Any) -> None:
    email = _address()
    _, code = _request(db_session, email)
    svc.purge_expired_codes(db_session, older_than_hours=24)
    assert svc.verify_code(db_session, email, code, settings=_settings()).email == email


def test_purge_disabled_when_retention_non_positive(db_session: Any) -> None:
    """A 0 or negative `older_than_hours` must no-op, never wipe the table.

    The cutoff is `now - older_than_hours`, so a non-positive value collapses it
    to "now" — every row, including one minted a moment ago, has
    `created_at < now` and would match. Review finding on #1136 (scored 95):
    mirrors the `<retention> <= 0` -> 0, untouched-DB contract every sibling
    sweep enforces (`purge_expired_sample_failures` / `sweep_orphan_assets` /
    `sweep_orphan_secrets` and their own `test_disabled_when_retention_non_positive`).
    """
    email = _address()
    _, code = _request(db_session, email)  # a LIVE, unexpired code

    assert svc.purge_expired_codes(db_session, older_than_hours=0) == 0
    assert svc.purge_expired_codes(db_session, older_than_hours=-1) == 0
    # Still there, and still verifiable — a non-positive window must not have
    # touched the row at all, let alone consumed it.
    assert svc.verify_code(db_session, email, code, settings=_settings()).email == email


# ── the concurrency branches ─────────────────────────────────────────────────
#
# These are the branches the caps actually rest on, and they are unreachable by
# calling the service twice in sequence — the losing interleaving has to be
# constructed. Each test below drives a REAL second write into the real database
# at the exact point between two statements where the race lives; only the
# scheduling is simulated, never the logic under test.


class _InterleavingSession:
    """Wraps the test session and runs `hook` once, after the Nth `execute`.

    That is the whole simulation: a second request lands between two of
    `verify_code`'s statements. Everything else — the SQL, the row locks, the
    predicates — is the production path against real Postgres.
    """

    def __init__(self, real: Any, *, after_execute: int, hook: Any) -> None:
        self._real = real
        self._after = after_execute
        self._hook = hook
        self._calls = 0

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        self._calls += 1
        result = self._real.execute(*args, **kwargs)
        if self._calls == self._after:
            self._hook()
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def _consume_now(db: Any, email: str) -> None:
    db.query(OtpCode).filter(OtpCode.email == email, OtpCode.consumed_at.is_(None)).update(
        {"consumed_at": datetime.now(UTC)}
    )
    db.commit()


def test_a_code_consumed_between_the_read_and_the_increment_is_rejected(db_session: Any) -> None:
    """The `consumed_at IS NULL` predicate on the incrementing UPDATE is what makes
    single-use race-proof. Without it, two concurrent redemptions of one code both
    succeed — and "single use" becomes "single use, usually"."""
    email = _address()
    _, code = _request(db_session, email)
    session = _InterleavingSession(
        db_session, after_execute=1, hook=lambda: _consume_now(db_session, email)
    )

    with pytest.raises(svc.OtpVerifyError):
        svc.verify_code(session, email, code, settings=_settings())  # type: ignore[arg-type]


def test_a_code_consumed_between_the_increment_and_the_consume_is_rejected(
    db_session: Any,
) -> None:
    """The second half of the same guarantee: the RIGHT code, presented by the
    loser of a race, must still fail. It is the conditional consume — not the hash
    comparison — that decides which of two correct guesses wins."""
    email = _address()
    _, code = _request(db_session, email)
    session = _InterleavingSession(
        db_session, after_execute=2, hook=lambda: _consume_now(db_session, email)
    )

    with pytest.raises(svc.OtpVerifyError):
        svc.verify_code(session, email, code, settings=_settings())  # type: ignore[arg-type]


def test_two_first_ever_signins_for_one_address_resolve_to_ONE_row(db_session: Any) -> None:
    """`uq_users_email_lower` rejects the loser's INSERT; the loser must then adopt
    the winner's row rather than 500.

    The rule is "one row per email", not "my INSERT wins" — and the alternative
    (an unhandled IntegrityError) would be a 500 on a first sign-in, which is the
    worst possible moment for one.
    """
    from sqlalchemy.exc import IntegrityError

    email = _address()
    winner = User(id=uuid.uuid4(), aad_object_id=None, email=email)

    real_commit = db_session.commit
    calls = {"n": 0}

    def _commit_that_loses_the_race_once() -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            # The winner's row lands first, exactly as another worker's would.
            db_session.rollback()
            db_session.add(winner)
            real_commit()
            raise IntegrityError("uq_users_email_lower", None, Exception())
        real_commit()

    db_session.commit = _commit_that_loses_the_race_once
    try:
        resolved = svc.resolve_or_create_user(db_session, email)
    finally:
        db_session.commit = real_commit

    assert resolved.id == winner.id
    assert db_session.query(User).filter(User.email == email).count() == 1


# ── the production counter store ─────────────────────────────────────────────


def test_the_redis_counter_store_bounds_its_socket_timeouts(monkeypatch: Any) -> None:
    """`redis.from_url` defaults BOTH timeouts to `None` — block forever. On the
    sign-in path that hangs a request thread instead of failing open, which is the
    #854 shape reintroduced in a new place."""
    import sys
    import types

    captured: dict[str, Any] = {}

    class _Pipe:
        def incr(self, key: str) -> None:
            captured["key"] = key

        def expire(self, key: str, ttl: int) -> None:
            captured["ttl"] = ttl

        def execute(self) -> list[int]:
            return [7, 1]

    class _Client:
        def pipeline(self) -> _Pipe:
            return _Pipe()

    fake_redis = types.ModuleType("redis")

    def _from_url(url: str, **kwargs: Any) -> _Client:
        captured["url"] = url
        captured["kwargs"] = kwargs
        return _Client()

    fake_redis.from_url = _from_url  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "redis", fake_redis)

    store = svc.RedisOtpCounterStore("redis://localhost:6379/0")
    assert store.incr_window("otp:req:abc:1", 1200) == 7
    assert captured["kwargs"]["socket_connect_timeout"] > 0
    assert captured["kwargs"]["socket_timeout"] > 0
    assert captured["ttl"] == 1200
    # The client is built once and reused, not per request.
    store.incr_window("otp:req:abc:1", 1200)
    assert captured["url"] == "redis://localhost:6379/0"


def test_the_redis_counter_store_fails_OPEN_on_any_error(monkeypatch: Any) -> None:
    import sys
    import types

    class _Client:
        def pipeline(self) -> Any:
            raise ConnectionError("redis is down")

    fake_redis = types.ModuleType("redis")
    fake_redis.from_url = lambda url, **kwargs: _Client()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "redis", fake_redis)

    assert svc.RedisOtpCounterStore("redis://x").incr_window("k", 1200) is None


def test_the_default_counter_store_is_the_redis_one() -> None:
    """The in-memory store must NEVER become an automatic production fallback: it
    would fragment the cap per replica while looking like enforcement."""
    svc.reset_counter_state()
    assert isinstance(svc.get_counter_store(), svc.RedisOtpCounterStore)
    svc.reset_counter_state()
