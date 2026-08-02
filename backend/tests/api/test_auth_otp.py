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
from backend.app.core.secrets import (
    SecretNotFoundError,
    SecretStoreUnavailableError,
    get_secret_store,
)
from backend.app.db.models import OtpCode, User, UserSession
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import otp_service, session_service

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


class _Store:
    def __init__(self, value: str | Exception = "app-password") -> None:
        self._value = value

    def get(self, name: str) -> str:
        if isinstance(self._value, Exception):
            raise self._value
        return self._value


def _otp_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "auth_email_smtp_host": "smtp.example.com",
        "auth_email_username": "dataq@example.com",
        "auth_email_from": "dataq@example.com",
        "auth_email_password_secret_name": "auth-email-password",
        "auth_otp_allowed_domains": "acme.io",
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def otp_env(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """The app wired for OTP mode: our DB session, a stub secret store, a captured
    SMTP transport, and an in-memory per-email counter."""
    import smtplib

    state: dict[str, Any] = {"settings": _otp_settings(), "store": _Store()}
    _CapturingSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", _CapturingSMTP)
    otp_service.set_counter_store_for_testing(otp_service.InMemoryOtpCounterStore())

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_settings] = lambda: state["settings"]
    app.dependency_overrides[get_secret_store] = lambda: state["store"]
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
    otp_env["store"] = _Store(SecretNotFoundError("not set"))
    not_set = client.post(REQUEST_URL, json={"email": _address()})

    otp_env["store"] = _Store(SecretStoreUnavailableError("sealed"))
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
