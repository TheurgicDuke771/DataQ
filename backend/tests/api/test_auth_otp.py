"""The three OTP endpoints over real HTTP (#734).

The property most of this file exists to defend is **anti-enumeration** (ADR 0032
decision 4): eligible, ineligible and throttled requests must be indistinguishable
to the caller. That is asserted on the raw response BYTES plus the status code and
headers — not on "both are 200" — because the leak that matters is any observable
difference at all.

The other half is the cookie: HttpOnly, SameSite=Lax, `Path=/`, no Domain, and
`Secure` conditioned on the deployment rather than hard-coded (the dev-vs-prod
footgun that silently drops the cookie on a plain-HTTP stack).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import Settings, get_settings
from backend.app.core.secrets import SecretNotFoundError, SecretStoreUnavailableError
from backend.app.db.models import OtpCode, User, UserSession
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import otp_service, session_service
from backend.tests.support.fake_secret_store import FakeSecretStore, override_secret_store

REQUEST_URL = "/api/v1/auth/otp/request"
VERIFY_URL = "/api/v1/auth/otp/verify"
LOGOUT_URL = "/api/v1/auth/logout"


class _CapturingSMTP:
    """Captures the outbound message instead of speaking SMTP. Class-level so a
    test can read what (if anything) the endpoint sent."""

    sent: ClassVar[list[str]] = []

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        pass

    def __enter__(self) -> _CapturingSMTP:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def starttls(self, context: Any = None) -> None:
        return None

    def login(self, user: str, password: str) -> None:
        return None

    def send_message(self, message: Any) -> None:
        _CapturingSMTP.sent.append(message.get_content())


def _otp_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "auth_email_smtp_host": "smtp.example.com",
        "auth_email_username": "dataq@example.com",
        "auth_email_from": "dataq@example.com",
        "auth_email_password_secret_name": "auth-email-password",
        "auth_otp_allowed_domains": "acme.io",
        # The constant-time floor (#1137) is OFF for the rest of this file: it is a
        # deliberate *sleep* on every uniform response, and paying the production
        # default (1s) on ~40 requests would add a minute to the suite for no signal.
        # The floor's own tests set it explicitly, and one of them pins the default,
        # so switching it off here cannot hide a regression in the shipped value.
        "auth_otp_request_min_seconds": 0,
        # Same reasoning for the verify-side floor (#1141) — it sleeps on every 401,
        # and this file raises a lot of them. Its own tests set it explicitly and one
        # pins the shipped default.
        "auth_otp_verify_min_seconds": 0,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def otp_env(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """The app wired for OTP mode: our DB session, a stub secret store, a captured
    SMTP transport, and an in-memory per-email counter."""
    import smtplib

    state: dict[str, Any] = {
        "settings": _otp_settings(),
        "store": FakeSecretStore(default="app-password"),
    }
    _CapturingSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", _CapturingSMTP)
    otp_service.set_counter_store_for_testing(otp_service.InMemoryOtpCounterStore())

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_settings] = lambda: state["settings"]
    # A test may swap `otp_env["store"]` mid-test (see
    # test_a_missing_password_secret_and_a_sealed_vault_are_DIFFERENT_errors
    # below); it re-invokes override_secret_store at the swap point to point the
    # override at the new store, rather than this fixture reading through the
    # dict on every call.
    override_secret_store(app, state["store"])
    try:
        yield state
    finally:
        app.dependency_overrides.clear()
        otp_service.reset_counter_state()


@pytest.fixture
def client(otp_env: dict[str, Any]) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _address() -> str:
    return f"user-{uuid.uuid4().hex[:10]}@acme.io"


def _last_code(db: Any, email: str) -> str:
    """The plaintext code, recovered by brute-forcing the stored hash.

    Only 10^6 candidates, and the test knows the address — which is precisely the
    point being made elsewhere about entropy. Cheaper than threading a capture hook
    through the mailer, and it exercises the real stored value.
    """
    row = (
        db.query(OtpCode)
        .filter(OtpCode.email == email, OtpCode.consumed_at.is_(None))
        .order_by(OtpCode.created_at.desc())
        .first()
    )
    assert row is not None, "no live code row was stored"
    body = _CapturingSMTP.sent[-1]
    for token in body.split():
        if token.isdigit() and len(token) == otp_service.CODE_DIGITS:
            assert otp_service._hash_code(token) == row.code_hash
            return token
    raise AssertionError("no code found in the sent message")


# ── anti-enumeration ─────────────────────────────────────────────────────────


def test_an_eligible_request_returns_ok_and_mails_a_code(client: TestClient) -> None:
    response = client.post(REQUEST_URL, json={"email": _address()})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert len(_CapturingSMTP.sent) == 1


def test_eligible_ineligible_and_throttled_are_BYTE_IDENTICAL(
    client: TestClient, otp_env: dict[str, Any]
) -> None:
    """The whole anti-enumeration property, asserted on raw bytes.

    Comparing `.json()` would miss a difference in key order or whitespace, and
    comparing only the status would miss a body that named the reason. An attacker
    diffs responses, not our intentions.
    """
    otp_env["settings"] = _otp_settings(auth_otp_request_per_email_per_10min=1)

    eligible = client.post(REQUEST_URL, json={"email": _address()})

    ineligible = client.post(
        REQUEST_URL, json={"email": f"stranger-{uuid.uuid4().hex[:8]}@elsewhere.example"}
    )

    throttled_address = _address()
    client.post(REQUEST_URL, json={"email": throttled_address})  # spends the budget
    throttled = client.post(REQUEST_URL, json={"email": throttled_address})

    assert eligible.status_code == ineligible.status_code == throttled.status_code == 200
    assert eligible.content == ineligible.content == throttled.content
    assert eligible.headers.get("content-type") == ineligible.headers.get("content-type")
    assert throttled.headers.get("content-type") == eligible.headers.get("content-type")


def test_an_ineligible_address_is_never_mailed(client: TestClient) -> None:
    client.post(REQUEST_URL, json={"email": f"stranger-{uuid.uuid4().hex[:8]}@elsewhere.example"})
    assert _CapturingSMTP.sent == []


def test_a_throttled_address_is_not_mailed_again(
    client: TestClient, otp_env: dict[str, Any]
) -> None:
    """A 429 here would be a perfect oracle — an ineligible address is never
    counted at all, so "did it throttle?" answers "is it allow-listed?"."""
    otp_env["settings"] = _otp_settings(auth_otp_request_per_email_per_10min=1)
    email = _address()
    client.post(REQUEST_URL, json={"email": email})
    client.post(REQUEST_URL, json={"email": email})
    assert len(_CapturingSMTP.sent) == 1


# ── request: input hostility ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "ada@acme.io\x00"},
        {"email": "\x00"},
        {"email": "a" * 400 + "@acme.io"},
        {"email": ""},
        {"email": None},
        {},
        {"email": ["ada@acme.io"]},
        {"email": {"nested": "ada@acme.io"}},
    ],
)
def test_hostile_request_payloads_are_422_never_500(client: TestClient, payload: Any) -> None:
    """NUL bytes and oversize input must never reach Postgres as a driver
    ValueError → 500 (#567). `ApiModel` + the length caps do this at the boundary."""
    response = client.post(REQUEST_URL, json=payload)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] in {"validation_error", "http_error"}


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "ada@acme.io", "code": "12345\x00"},
        {"email": "ada@acme.io", "code": "1" * 100},
        {"email": "ada@acme.io"},
        {"code": "123456"},
    ],
)
def test_hostile_verify_payloads_are_422_never_500(client: TestClient, payload: Any) -> None:
    response = client.post(VERIFY_URL, json=payload)
    assert response.status_code == 422, response.text


def test_a_unicode_code_is_a_401_not_a_500(client: TestClient) -> None:
    """`hmac.compare_digest` raises TypeError on non-ASCII `str` — the trap the
    orchestration webhooks document. A 500 here would be a DoS on the verify path."""
    email = _address()
    client.post(REQUEST_URL, json={"email": email})
    response = client.post(VERIFY_URL, json={"email": email, "code": "üñîçø"})
    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "invalid_otp_code"


# ── request: transport failures surface ──────────────────────────────────────


def test_an_smtp_failure_surfaces_as_502_with_no_quiet_no_op(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#734 AC. This is also the ONE place the uniform response is not uniform —
    documented in the module docstring of `api/v1/auth_otp.py`, and accepted
    because it only diverges while the mail server is down."""
    import smtplib

    class _BrokenSMTP(_CapturingSMTP):
        def send_message(self, message: Any) -> None:
            raise smtplib.SMTPServerDisconnected("relay went away")

    monkeypatch.setattr(smtplib, "SMTP", _BrokenSMTP)
    response = client.post(REQUEST_URL, json={"email": _address()})
    assert response.status_code == 502, response.text
    assert response.json()["error"]["code"] == "otp_email_send_failed"


def test_a_missing_password_secret_and_a_sealed_vault_are_DIFFERENT_errors(
    client: TestClient, otp_env: dict[str, Any]
) -> None:
    """ADR 0039 decision 6 through the HTTP layer: an outage is never reported as
    "not configured", because the fixes are different."""
    otp_env["store"] = FakeSecretStore(raise_on_get=SecretNotFoundError("not set"))
    override_secret_store(app, otp_env["store"])
    not_set = client.post(REQUEST_URL, json={"email": _address()})

    otp_env["store"] = FakeSecretStore(raise_on_get=SecretStoreUnavailableError("sealed"))
    override_secret_store(app, otp_env["store"])
    outage = client.post(REQUEST_URL, json={"email": _address()})

    assert not_set.json()["error"]["code"] == "otp_email_not_configured"
    assert outage.json()["error"]["code"] == "secret_store_unavailable"
    assert not_set.content != outage.content


def test_the_endpoints_503_when_otp_is_not_configured(
    client: TestClient, otp_env: dict[str, Any]
) -> None:
    otp_env["settings"] = Settings()  # no OTP block at all
    for url, payload in (
        (REQUEST_URL, {"email": "a@b.io"}),
        (VERIFY_URL, {"email": "a@b.io", "code": "1"}),
    ):
        response = client.post(url, json=payload)
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "otp_not_configured"


# ── request: the constant-time floor (#1137) ─────────────────────────────────
#
# The uniform BODY hid eligibility; the response TIME gave it back. An eligible
# address pays Redis + two DB writes + a synchronous SMTP handshake; an ineligible
# one pays a single in-memory set lookup. These tests use a mailer that returns
# instantly — the WORST case for the property, since it makes the eligible path as
# cheap as it can ever be, so anything the floor fails to cover shows up here.

#: Short enough to keep the suite quick, long enough to dwarf handler overhead.
_FLOOR = 0.4


def test_an_ineligible_response_is_held_as_long_as_an_eligible_one(
    client: TestClient, otp_env: dict[str, Any]
) -> None:
    """The enumeration channel, closed. Both branches must clear the floor —
    asserting only "the eligible one is slow" would pass with no floor at all."""
    import time

    otp_env["settings"] = _otp_settings(auth_otp_request_min_seconds=_FLOOR)

    started = time.monotonic()
    eligible = client.post(REQUEST_URL, json={"email": _address()})
    eligible_elapsed = time.monotonic() - started

    started = time.monotonic()
    ineligible = client.post(
        REQUEST_URL, json={"email": f"stranger-{uuid.uuid4().hex[:8]}@elsewhere.example"}
    )
    ineligible_elapsed = time.monotonic() - started

    assert eligible.status_code == ineligible.status_code == 200
    assert eligible_elapsed >= _FLOOR, f"eligible answered in {eligible_elapsed:.3f}s"
    assert ineligible_elapsed >= _FLOOR, f"ineligible answered in {ineligible_elapsed:.3f}s"


def test_a_throttled_response_is_held_to_the_floor_too(
    client: TestClient, otp_env: dict[str, Any]
) -> None:
    """Throttled is the third uniform branch, and the cheapest of the three once the
    counter says no — it must not become the tell."""
    import time

    otp_env["settings"] = _otp_settings(
        auth_otp_request_min_seconds=_FLOOR, auth_otp_request_per_email_per_10min=1
    )
    email = _address()
    client.post(REQUEST_URL, json={"email": email})  # spends the budget

    started = time.monotonic()
    throttled = client.post(REQUEST_URL, json={"email": email})
    elapsed = time.monotonic() - started

    assert throttled.status_code == 200
    assert elapsed >= _FLOOR, f"throttled answered in {elapsed:.3f}s"


def test_the_floor_is_applied_once_not_twice(client: TestClient, otp_env: dict[str, Any]) -> None:
    """A floor applied in two places (service AND endpoint, say) would still hide
    eligibility — and would double every sign-in's latency while looking correct.
    The bound is the tell: one floor, not two."""
    import time

    otp_env["settings"] = _otp_settings(auth_otp_request_min_seconds=_FLOOR)

    started = time.monotonic()
    client.post(REQUEST_URL, json={"email": _address()})
    elapsed = time.monotonic() - started

    assert _FLOOR <= elapsed < 2 * _FLOOR, f"{elapsed:.3f}s is not one floor's worth"


def test_an_error_response_is_NOT_padded(
    client: TestClient, otp_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Error responses are already non-uniform by design (a mail outage is a
    deployment-wide, operator-visible condition — ADR 0032 §7), so padding them
    would buy nothing and would hold a worker thread for the whole outage. Pinned
    rather than left implicit, because "we pad everything" is the tempting mistake.
    """
    import smtplib
    import time

    class _BrokenSMTP(_CapturingSMTP):
        def send_message(self, message: Any) -> None:
            raise smtplib.SMTPServerDisconnected("relay went away")

    monkeypatch.setattr(smtplib, "SMTP", _BrokenSMTP)
    otp_env["settings"] = _otp_settings(auth_otp_request_min_seconds=2.0)

    started = time.monotonic()
    response = client.post(REQUEST_URL, json={"email": _address()})
    elapsed = time.monotonic() - started

    assert response.status_code == 502
    assert elapsed < 1.0, f"the 502 waited out the floor ({elapsed:.3f}s)"


def test_the_floor_can_be_switched_off(client: TestClient, otp_env: dict[str, Any]) -> None:
    """0 means no sleep at all — a dev/test escape hatch, and the documented cost is
    that the timing channel is fully open again."""
    import time

    otp_env["settings"] = _otp_settings(auth_otp_request_min_seconds=0)

    started = time.monotonic()
    client.post(REQUEST_URL, json={"email": _address()})

    assert time.monotonic() - started < 0.3


def test_the_shipped_floor_default_is_one_second() -> None:
    """The value that actually protects a deployment is the DEFAULT — every test
    above overrides it, so without this the shipped number is unasserted."""
    assert Settings().auth_otp_request_min_seconds == 1.0


@pytest.mark.parametrize(
    ("started", "now", "expected"),
    [
        (100.0, 100.0, 0.4),  # instant work → hold the whole floor
        (100.0, 100.3, pytest.approx(0.1)),  # partial work → hold the REMAINDER
        (100.0, 100.4, 0.0),  # exactly at the floor → nothing left
        (100.0, 101.5, 0.0),  # a slow relay overran it → never negative
    ],
)
def test_the_remainder_is_what_is_left_of_the_floor(
    started: float, now: float, expected: float
) -> None:
    """Sleeping a FIXED amount after variable work just shifts the distribution and
    leaves the eligible/ineligible difference intact — the pad has to be the
    remainder. Clamped at zero so an overrun never sleeps a negative."""
    from backend.app.api.v1.auth_otp import _floor_remainder

    assert _floor_remainder(started, _FLOOR, now=now) == expected


# ── verify: the same floor, one endpoint over (#1141) ────────────────────────
#
# `otp/verify` answers a byte-identical 401 for every failure, but the WORK behind
# it splits on eligibility: an address with a live code runs `UPDATE … RETURNING`
# plus a commit before the hash compare, while an address with none returns off the
# first `SELECT`. Two requests is the whole attack — `otp/request` for the target
# (uniform `ok`, tells you nothing) mints the row, then `verify` with any wrong code
# times it.

#: Same shape as `_FLOOR`, its own name so the two floors can never be confused.
_VERIFY_FLOOR = 0.4


def _verify_floor_settings() -> Settings:
    """Verify-side floor ON, request-side floor OFF — the setup `otp/request` call
    is not what is being measured and must not add a second's wait to each test."""
    return _otp_settings(auth_otp_verify_min_seconds=_VERIFY_FLOOR)


def test_a_wrong_code_is_held_to_the_floor_WITH_and_WITHOUT_a_live_code(
    client: TestClient, otp_env: dict[str, Any]
) -> None:
    """The #1141 channel, closed. Asserting only "the address with a live code is
    slower" would pass with no floor at all — BOTH 401s have to clear it, and the
    responses have to stay byte-identical while they do.
    """
    import time

    otp_env["settings"] = _verify_floor_settings()

    with_code = _address()
    client.post(REQUEST_URL, json={"email": with_code})  # mints a live code row
    without_code = _address()  # eligible, but never requested one

    started = time.monotonic()
    live = client.post(VERIFY_URL, json={"email": with_code, "code": "000000"})
    live_elapsed = time.monotonic() - started

    started = time.monotonic()
    none = client.post(VERIFY_URL, json={"email": without_code, "code": "000000"})
    none_elapsed = time.monotonic() - started

    assert live.status_code == none.status_code == 401
    assert live.content == none.content
    assert live_elapsed >= _VERIFY_FLOOR, f"live-code 401 answered in {live_elapsed:.3f}s"
    assert none_elapsed >= _VERIFY_FLOOR, f"no-code 401 answered in {none_elapsed:.3f}s"


def test_an_ineligible_address_is_held_to_the_verify_floor_too(
    client: TestClient, otp_env: dict[str, Any]
) -> None:
    """The address an attacker actually probes is one they suspect is NOT allow-listed
    — its 401 comes off the cheapest path of all (no row can exist), so it is the
    branch most likely to become the tell."""
    import time

    otp_env["settings"] = _verify_floor_settings()

    started = time.monotonic()
    response = client.post(
        VERIFY_URL,
        json={"email": f"stranger-{uuid.uuid4().hex[:8]}@elsewhere.example", "code": "000000"},
    )
    elapsed = time.monotonic() - started

    assert response.status_code == 401
    assert elapsed >= _VERIFY_FLOOR, f"ineligible 401 answered in {elapsed:.3f}s"


def test_a_SUCCESSFUL_verification_is_NOT_padded(
    client: TestClient, otp_env: dict[str, Any], db_session: Any
) -> None:
    """The decision, pinned: only the uniform 401 is floored. A 200 already separates
    itself from a 401, and its caller knows the code by definition — so padding it
    would tax every real sign-in and hide nothing. "Pad everything" is the tempting
    mistake, and it is the one that makes sign-in feel broken."""
    import time

    # A floor far larger than the assertion's bound, so "not padded" cannot pass by
    # the sleep merely being short.
    otp_env["settings"] = _otp_settings(auth_otp_verify_min_seconds=2.0)
    email = _address()
    client.post(REQUEST_URL, json={"email": email})
    code = _last_code(db_session, email)

    started = time.monotonic()
    response = client.post(VERIFY_URL, json={"email": email, "code": code})
    elapsed = time.monotonic() - started

    assert response.status_code == 200, response.text
    assert elapsed < 1.0, f"the successful sign-in waited out the floor ({elapsed:.3f}s)"


def test_the_verify_floor_is_applied_once_not_twice(
    client: TestClient, otp_env: dict[str, Any]
) -> None:
    """One floor, not two — a second pad anywhere on the path would still hide
    eligibility while doubling the wait, so the upper bound is the tell."""
    import time

    otp_env["settings"] = _verify_floor_settings()

    started = time.monotonic()
    client.post(VERIFY_URL, json={"email": _address(), "code": "000000"})
    elapsed = time.monotonic() - started

    assert _VERIFY_FLOOR <= elapsed < 2 * _VERIFY_FLOOR, f"{elapsed:.3f}s is not one floor's worth"


def test_the_unconfigured_503_is_NOT_padded_on_verify(
    client: TestClient, otp_env: dict[str, Any]
) -> None:
    """A deployment with OTP switched off answers 503 for EVERY address, so it
    carries no per-address signal — and holding a worker thread for it would make an
    unconfigured deployment slow as well as unusable."""
    import time

    otp_env["settings"] = Settings(auth_otp_verify_min_seconds=2.0)  # no AUTH_EMAIL_* block

    started = time.monotonic()
    response = client.post(VERIFY_URL, json={"email": _address(), "code": "000000"})
    elapsed = time.monotonic() - started

    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "otp_not_configured"
    assert elapsed < 1.0, f"the 503 waited out the floor ({elapsed:.3f}s)"


def test_the_verify_floor_can_be_switched_off(client: TestClient, otp_env: dict[str, Any]) -> None:
    """0 means no sleep at all — the dev/test escape hatch, with the documented cost
    that the #1141 timing channel is fully open again."""
    import time

    otp_env["settings"] = _otp_settings(auth_otp_verify_min_seconds=0)

    started = time.monotonic()
    response = client.post(VERIFY_URL, json={"email": _address(), "code": "000000"})

    assert response.status_code == 401
    assert time.monotonic() - started < 0.3


def test_the_shipped_verify_floor_default_is_half_a_second() -> None:
    """The value that actually protects a deployment is the DEFAULT — every test
    above overrides it, so without this the shipped number is unasserted."""
    assert Settings().auth_otp_verify_min_seconds == 0.5


# ── verify → cookie ──────────────────────────────────────────────────────────


def test_verify_sets_an_httponly_lax_root_path_cookie(client: TestClient, db_session: Any) -> None:
    email = _address()
    client.post(REQUEST_URL, json={"email": email})
    code = _last_code(db_session, email)

    response = client.post(VERIFY_URL, json={"email": email, "code": code})
    assert response.status_code == 200, response.text

    jar = SimpleCookie()
    jar.load(response.headers["set-cookie"])
    morsel = jar[session_service.COOKIE_NAME]
    assert morsel.value.startswith(session_service.TOKEN_PREFIX)
    assert morsel["httponly"], "the SPA could read the token out of document.cookie"
    assert morsel["samesite"].lower() == "lax"
    assert morsel["path"] == "/"
    # Domain must stay UNSET: nginx forwards the UPSTREAM Host, so a derived
    # Domain would scope the cookie to an internal hostname the browser never saw.
    assert not morsel["domain"]
    assert morsel["max-age"]


def test_the_session_token_is_not_in_the_response_body(client: TestClient, db_session: Any) -> None:
    email = _address()
    client.post(REQUEST_URL, json={"email": email})
    response = client.post(VERIFY_URL, json={"email": email, "code": _last_code(db_session, email)})
    assert session_service.TOKEN_PREFIX not in response.text


def test_verify_returns_the_me_shape(client: TestClient, db_session: Any) -> None:
    email = _address()
    client.post(REQUEST_URL, json={"email": email})
    body = client.post(
        VERIFY_URL, json={"email": email, "code": _last_code(db_session, email)}
    ).json()
    assert body["email"] == email
    assert body["aad_object_id"] is None
    assert body["is_workspace_admin"] is False
    assert "id" in body


def test_the_workspace_admin_flag_comes_through(
    client: TestClient, db_session: Any, make_workspace_admin: Any
) -> None:
    """Via `WORKSPACE_ADMIN_EMAILS` in the environment, not a request-scoped
    settings override, because that is how the flag is actually resolved:
    `is_workspace_admin` reads the process-wide `get_settings()` — the same path
    `/me` takes — so an override injected per request would prove nothing about
    production. It also pins the case-insensitive match against a normalized
    OTP-provisioned address."""
    email = _address()
    make_workspace_admin(email.upper())
    client.post(REQUEST_URL, json={"email": email})
    body = client.post(
        VERIFY_URL, json={"email": email, "code": _last_code(db_session, email)}
    ).json()
    assert body["is_workspace_admin"] is True


@pytest.mark.parametrize(
    ("headers", "explicit", "expect_secure"),
    [
        ({}, None, False),  # plain-HTTP dev — a hard-coded Secure would DROP the cookie
        ({"X-Forwarded-Proto": "https"}, None, True),  # behind the TLS-terminating proxy
        ({"X-Forwarded-Proto": "https, http"}, None, True),  # client-facing hop is first
        ({"X-Forwarded-Proto": "http"}, None, False),
        ({}, True, True),  # explicit override wins
        ({"X-Forwarded-Proto": "https"}, False, False),
    ],
)
def test_the_secure_flag_follows_the_deployment_not_a_hardcoded_constant(
    client: TestClient,
    db_session: Any,
    otp_env: dict[str, Any],
    headers: dict[str, str],
    explicit: bool | None,
    expect_secure: bool,
) -> None:
    """The single most likely dev-vs-prod footgun in this feature: with `Secure`
    hard-coded on, a plain-HTTP dev stack accepts the `Set-Cookie` and then never
    sends the cookie back — a successful sign-in followed by a 401, with nothing
    in any log to explain it."""
    otp_env["settings"] = _otp_settings(auth_session_cookie_secure=explicit)
    email = _address()
    client.post(REQUEST_URL, json={"email": email}, headers=headers)
    response = client.post(
        VERIFY_URL,
        json={"email": email, "code": _last_code(db_session, email)},
        headers=headers,
    )
    jar = SimpleCookie()
    jar.load(response.headers["set-cookie"])
    assert bool(jar[session_service.COOKIE_NAME]["secure"]) is expect_secure


def test_a_wrong_code_sets_no_cookie(client: TestClient) -> None:
    email = _address()
    client.post(REQUEST_URL, json={"email": email})
    response = client.post(VERIFY_URL, json={"email": email, "code": "000000"})
    assert response.status_code == 401
    assert "set-cookie" not in {k.lower() for k in response.headers}


def test_a_verified_code_cannot_be_replayed_for_a_second_session(
    client: TestClient, db_session: Any
) -> None:
    email = _address()
    client.post(REQUEST_URL, json={"email": email})
    code = _last_code(db_session, email)
    first = client.post(VERIFY_URL, json={"email": email, "code": code})
    assert first.status_code == 200
    replay = client.post(VERIFY_URL, json={"email": email, "code": code})
    assert replay.status_code == 401
    assert db_session.query(UserSession).count() == 1


# ── logout ───────────────────────────────────────────────────────────────────


def test_logout_revokes_the_session_and_clears_the_cookie(
    client: TestClient, db_session: Any
) -> None:
    email = _address()
    client.post(REQUEST_URL, json={"email": email})
    client.post(VERIFY_URL, json={"email": email, "code": _last_code(db_session, email)})
    token = client.cookies.get(session_service.COOKIE_NAME)
    assert token is not None

    response = client.post(LOGOUT_URL)
    assert response.status_code == 204
    assert "set-cookie" in {k.lower() for k in response.headers}

    row = db_session.query(UserSession).one()
    assert row.revoked_at is not None
    # And the token no longer authenticates — enforced at the seam, not just stored.
    with pytest.raises(session_service.SessionAuthError):
        session_service.resolve_token(db_session, token)


def test_logout_is_idempotent_and_works_with_no_cookie(client: TestClient) -> None:
    """A 401 here would strand a stale cookie in the browser forever."""
    no_cookie = client.post(LOGOUT_URL)
    assert no_cookie.status_code == 204

    client.cookies.set(session_service.COOKIE_NAME, session_service.TOKEN_PREFIX + "garbage")
    unknown_token = client.post(LOGOUT_URL)
    assert unknown_token.status_code == 204

    client.cookies.set(session_service.COOKIE_NAME, "not-even-prefixed")
    malformed = client.post(LOGOUT_URL)
    assert malformed.status_code == 204


def test_logout_cannot_revoke_another_users_session(client: TestClient, db_session: Any) -> None:
    """Logout keys on the presented TOKEN, never on a user id from the body — so
    there is nothing to forge."""
    victim = User(id=uuid.uuid4(), aad_object_id=uuid.uuid4().hex, email=_address())
    db_session.add(victim)
    db_session.commit()
    _, victim_token = session_service.create_session(db_session, victim)

    client.post(LOGOUT_URL)  # attacker, no cookie
    assert session_service.resolve_token(db_session, victim_token).id == victim.id


# ── linking, through HTTP ────────────────────────────────────────────────────


def test_signing_in_with_an_existing_AAD_users_address_resolves_to_that_row(
    client: TestClient, db_session: Any
) -> None:
    email = _address()
    aad_user = User(id=uuid.uuid4(), aad_object_id=uuid.uuid4().hex, email=email.upper())
    db_session.add(aad_user)
    db_session.commit()

    client.post(REQUEST_URL, json={"email": email})
    body = client.post(
        VERIFY_URL, json={"email": email, "code": _last_code(db_session, email)}
    ).json()

    assert body["id"] == str(aad_user.id)
    assert body["aad_object_id"] == aad_user.aad_object_id
    assert db_session.query(User).filter(User.email.ilike(email)).count() == 1


def test_an_expired_code_cannot_be_verified_over_http(client: TestClient, db_session: Any) -> None:
    email = _address()
    client.post(REQUEST_URL, json={"email": email})
    code = _last_code(db_session, email)
    row = db_session.query(OtpCode).filter(OtpCode.email == email).one()
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    response = client.post(VERIFY_URL, json={"email": email, "code": code})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_otp_code"


def test_the_attempt_cap_holds_over_http(client: TestClient, db_session: Any) -> None:
    email = _address()
    client.post(REQUEST_URL, json={"email": email})
    code = _last_code(db_session, email)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(otp_service.MAX_ATTEMPTS):
        attempt = client.post(VERIFY_URL, json={"email": email, "code": wrong})
        assert attempt.status_code == 401
    # …and the RIGHT code is now dead too.
    locked_out = client.post(VERIFY_URL, json={"email": email, "code": code})
    assert locked_out.status_code == 401
