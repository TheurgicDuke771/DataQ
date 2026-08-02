"""The OTP mailer's error contract — the opposite of the alert mailer's (#734).

`alerting/email.py` is best-effort by design: it no-ops when unconfigured and
swallows an unresolvable password, so a flaky channel can never fail a run. This
mailer must do the reverse, because it sits on the sign-in path — a user told
"check your email" when nothing was sent has no way to distinguish a slow relay
from a dead one.

The mocking boundary is `smtplib.SMTP` — the third-party transport — never the
service under test.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from typing import Any, ClassVar

import pytest

from backend.app.core.config import Settings
from backend.app.core.secrets import SecretNotFoundError, SecretStoreUnavailableError
from backend.app.services import otp_mailer


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "auth_email_smtp_host": "smtp.example.com",
        "auth_email_smtp_port": 587,
        "auth_email_username": "dataq@example.com",
        "auth_email_from": "DataQ <dataq@example.com>",
        "auth_email_password_secret_name": "auth-email-password",
        "auth_otp_allowed_domains": "acme.io",
    }
    base.update(overrides)
    return Settings(**base)


class _Store:
    """A `SecretStore` that answers `get` with a fixed value or a fixed failure."""

    def __init__(self, value: str | Exception) -> None:
        self._value = value

    def get(self, name: str) -> str:
        if isinstance(self._value, Exception):
            raise self._value
        return self._value

    # The mailer must only ever READ. These complete the Protocol and fail loudly
    # if it ever starts writing — a mailer that can mutate the secret store is a
    # much larger blast radius than the feature needs.
    def set(self, name: str, value: str) -> None:
        raise AssertionError("the OTP mailer must never write to the secret store")

    def delete(self, name: str) -> None:
        raise AssertionError("the OTP mailer must never delete from the secret store")


class _FakeSMTP:
    """Records the whole submission sequence, including the ORDER of the calls."""

    instances: ClassVar[list[_FakeSMTP]] = []

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self.calls: list[str] = []
        self.messages: list[EmailMessage] = []
        self.tls_context: ssl.SSLContext | None = None
        self.credentials: tuple[str, str] | None = None
        _FakeSMTP.instances.append(self)

    def __enter__(self) -> _FakeSMTP:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.calls.append("close")

    def starttls(self, context: ssl.SSLContext | None = None) -> None:
        self.calls.append("starttls")
        self.tls_context = context

    def login(self, user: str, password: str) -> None:
        self.calls.append("login")
        self.credentials = (user, password)

    def send_message(self, message: EmailMessage) -> None:
        self.calls.append("send")
        self.messages.append(message)


@pytest.fixture(autouse=True)
def _reset_fake() -> Any:
    _FakeSMTP.instances = []
    yield
    _FakeSMTP.instances = []


def _mailer(monkeypatch: pytest.MonkeyPatch, store: Any, **overrides: Any) -> otp_mailer.OtpMailer:
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    return otp_mailer.OtpMailer(store, _settings(**overrides))


def test_a_code_is_submitted_over_starttls_before_authenticating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """STARTTLS must precede LOGIN, or the password crosses the wire in the clear.

    Asserting the ORDER, not merely that both happened — a transport that logs in
    first and upgrades afterwards would satisfy a "starttls was called" test while
    leaking the credential on every send.
    """
    mailer = _mailer(monkeypatch, _Store("app-password"))
    mailer.send_code(to="ada@acme.io", code="123456", expires_in_minutes=10)

    smtp = _FakeSMTP.instances[-1]
    assert smtp.calls == ["starttls", "login", "send", "close"]
    assert isinstance(smtp.tls_context, ssl.SSLContext)
    assert smtp.credentials == ("dataq@example.com", "app-password")
    assert smtp.host == "smtp.example.com" and smtp.port == 587


def test_the_timeout_is_short_because_this_is_on_the_request_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mailer = _mailer(monkeypatch, _Store("pw"), auth_email_timeout_seconds=5.0)
    mailer.send_code(to="ada@acme.io", code="123456", expires_in_minutes=10)
    assert _FakeSMTP.instances[-1].timeout == 5.0


def test_the_message_carries_the_code_and_no_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR 0032 rejected magic links: mail scanners prefetch URLs (consuming
    single-use links) and links leak through referrers and logs. Nothing clickable
    may appear in this message."""
    mailer = _mailer(monkeypatch, _Store("pw"))
    mailer.send_code(to="ada@acme.io", code="424242", expires_in_minutes=10)

    message = _FakeSMTP.instances[-1].messages[0]
    body = message.get_content()
    assert "424242" in body
    assert "10 minutes" in body
    assert "http://" not in body and "https://" not in body and "www." not in body
    assert message["To"] == "ada@acme.io"
    assert message["From"] == "DataQ <dataq@example.com>"
    # Plain text only — no HTML alternative to render a link in.
    assert not message.is_multipart()


def test_the_password_never_reaches_the_message(monkeypatch: pytest.MonkeyPatch) -> None:
    mailer = _mailer(monkeypatch, _Store("s3cr3t-app-password"))
    mailer.send_code(to="ada@acme.io", code="123456", expires_in_minutes=10)
    body = _FakeSMTP.instances[-1].messages[0].get_content()
    assert "s3cr3t-app-password" not in body


@pytest.mark.parametrize(
    "failure",
    [
        smtplib.SMTPAuthenticationError(535, b"bad credentials"),
        smtplib.SMTPServerDisconnected("connection lost"),
        TimeoutError("timed out"),
        OSError("network unreachable"),
        ssl.SSLError("handshake failed"),
    ],
)
def test_every_transport_failure_is_a_502_never_a_silent_no_op(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    """smtplib raises OSError / TimeoutError / SSLError as well as its own family;
    the caller's contract is one 502 either way (#734 AC: no quiet no-op)."""

    class _BrokenSMTP(_FakeSMTP):
        def send_message(self, message: EmailMessage) -> None:
            raise failure

    monkeypatch.setattr(smtplib, "SMTP", _BrokenSMTP)
    mailer = otp_mailer.OtpMailer(_Store("pw"), _settings())

    with pytest.raises(otp_mailer.OtpMailSendError) as caught:
        mailer.send_code(to="ada@acme.io", code="123456", expires_in_minutes=10)
    assert caught.value.status_code == 502
    assert caught.value.code == "otp_email_send_failed"


def test_a_send_failure_logs_the_error_TYPE_but_not_the_recipient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator has to be able to tell a TLS failure from a refused login. The
    exception MESSAGE is withheld because SMTP servers echo the envelope — i.e.
    the user's address — into their rejection text, where `_PII_KEYS` cannot
    reach it (it redacts KEYS, not arbitrary substrings)."""
    import io
    import logging

    from backend.app.core.logging import configure_logging

    class _BrokenSMTP(_FakeSMTP):
        def send_message(self, message: EmailMessage) -> None:
            raise smtplib.SMTPRecipientsRefused({"ada@acme.io": (550, b"no such user ada@acme.io")})

    monkeypatch.setattr(smtplib, "SMTP", _BrokenSMTP)
    configure_logging()
    buffer = io.StringIO()
    handler = logging.getLogger().handlers[0]
    original = handler.stream  # type: ignore[attr-defined]
    handler.stream = buffer  # type: ignore[attr-defined]
    try:
        with pytest.raises(otp_mailer.OtpMailSendError):
            otp_mailer.OtpMailer(_Store("pw"), _settings()).send_code(
                to="ada@acme.io", code="123456", expires_in_minutes=10
            )
    finally:
        handler.stream = original  # type: ignore[attr-defined]

    emitted = buffer.getvalue()
    assert "otp_email_send_failed" in emitted
    assert "SMTPRecipientsRefused" in emitted, "the operator cannot tell what broke"
    assert "ada@acme.io" not in emitted


def test_a_missing_password_secret_is_NOT_the_same_error_as_a_sealed_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR 0039 decision 6, restated: an outage is never reportable as a state.

    Folding `SecretStoreUnavailableError` into "not configured" sends the operator
    to change a setting that was already correct — which is exactly the defect ADR
    0039 records for `AzureKeyVaultStore`, shipped since Week 2.
    """
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    settings = _settings()

    not_set = otp_mailer.OtpMailer(_Store(SecretNotFoundError("nope")), settings)
    with pytest.raises(otp_mailer.OtpMailNotConfiguredError) as missing:
        not_set.send_code(to="ada@acme.io", code="1", expires_in_minutes=10)

    sealed = otp_mailer.OtpMailer(_Store(SecretStoreUnavailableError("sealed")), settings)
    with pytest.raises(otp_mailer.OtpMailStoreUnavailableError) as outage:
        sealed.send_code(to="ada@acme.io", code="1", expires_in_minutes=10)

    assert missing.value.code == "otp_email_not_configured"
    assert outage.value.code == "secret_store_unavailable"
    assert missing.value.code != outage.value.code
    assert missing.value.message != outage.value.message
    # Neither may reach the mail server.
    assert _FakeSMTP.instances == []


def test_nothing_is_sent_when_the_transport_block_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defence-in-depth behind `otp_auth_configured` + the startup validator: a
    future caller must not be able to reach smtplib with a half-built transport.

    Built with `model_construct` because `Settings` itself refuses this state —
    which is the point, and is asserted directly in `test_otp_config.py`.
    """
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    broken = Settings.model_construct(
        auth_email_smtp_host=None,
        auth_email_username=None,
        auth_email_from=None,
        auth_email_password_secret_name=None,
        auth_email_smtp_port=587,
        auth_email_timeout_seconds=5.0,
    )
    with pytest.raises(otp_mailer.OtpMailNotConfiguredError):
        otp_mailer.OtpMailer(_Store("pw"), broken).send_code(
            to="ada@acme.io", code="1", expires_in_minutes=10
        )
    assert _FakeSMTP.instances == []
