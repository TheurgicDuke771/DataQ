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

import datetime as dt
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import Any, ClassVar

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

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

    def starttls(self, context: ssl.SSLContext | None = None) -> None:
        self.calls.append("starttls")
        self.tls_context = context

    def login(self, user: str, password: str) -> None:
        self.calls.append("login")
        self.credentials = (user, password)

    def send_message(self, message: EmailMessage) -> None:
        self.calls.append("send")
        self.messages.append(message)

    def quit(self) -> None:
        # `_deliver` closes explicitly via `.quit()` in a `finally`, never via
        # `with`/`__exit__` (#737 review — see `_deliver`'s docstring for why).
        self.calls.append("close")


class _FakeSMTPSSL(_FakeSMTP):
    """Implicit-TLS (:465) stand-in for `smtplib.SMTP_SSL`. Unlike `_FakeSMTP`,
    the handshake context arrives in the CONSTRUCTOR (`context=`) rather than
    through a separate `starttls()` call — that is the real difference between
    the two transports `_deliver` has to bridge (#1146).

    **Its own `ssl_instances` list**, deliberately NOT `_FakeSMTP.instances`: a
    `ClassVar` looked up through a subclass without its own binding resolves to
    the SAME list object, which would make "was `SMTP_SSL` ever used"
    unaskable — every `_FakeSMTP` instance would silently count as one too.
    """

    ssl_instances: ClassVar[list[_FakeSMTPSSL]] = []

    def __init__(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        context: ssl.SSLContext | None = None,
    ) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self.calls: list[str] = []
        self.messages: list[EmailMessage] = []
        self.tls_context: ssl.SSLContext | None = context
        self.credentials: tuple[str, str] | None = None
        _FakeSMTPSSL.ssl_instances.append(self)

    def starttls(self, context: ssl.SSLContext | None = None) -> None:  # pragma: no cover
        raise AssertionError("implicit mode must never call starttls() — TLS is from connect")


def _write_self_signed_ca(path: Path, common_name: str = "dataq-test-ca") -> None:
    """A throwaway self-signed CA cert, just enough shape for `cafile=` to load
    it: `BasicConstraints(ca=True)` and a subject `ssl.SSLContext.get_ca_certs()`
    can report back. Not a real trust anchor — never used to actually negotiate
    TLS in these tests, only to prove the path reached `create_default_context`.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


@pytest.fixture(autouse=True)
def _reset_fake() -> Any:
    _FakeSMTP.instances = []
    _FakeSMTPSSL.ssl_instances = []
    yield
    _FakeSMTP.instances = []
    _FakeSMTPSSL.ssl_instances = []


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


# ── TLS transport options (#1146) ────────────────────────────────────────────


def test_implicit_mode_uses_smtp_ssl_with_no_separate_starttls_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SMTPS (:465): the handshake happens INSIDE `SMTP_SSL.__init__`, so
    `_deliver` must never call `starttls()` on top of it — doing so against an
    already-TLS socket is exactly the failure #1146 reported."""
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTPSSL)
    mailer = otp_mailer.OtpMailer(
        _Store("pw"),
        _settings(auth_email_tls_mode="implicit", auth_email_smtp_port=465),
    )
    mailer.send_code(to="ada@acme.io", code="123456", expires_in_minutes=10)

    smtp = _FakeSMTPSSL.ssl_instances[-1]
    assert smtp.calls == ["login", "send", "close"]
    assert isinstance(smtp.tls_context, ssl.SSLContext)
    assert smtp.port == 465


def test_implicit_mode_classifies_a_handshake_failure_as_connect_not_tls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`SMTP_SSL`'s constructor does the TCP connect AND the TLS handshake
    together — there is no separate post-connect hook to fail on its own, so a
    certificate failure here is genuinely the 'connect' stage, never 'tls' (that
    stage is reachable only via the `starttls` branch). Documented in
    `_deliver`'s docstring; pinned here so the classification can't drift."""

    class _HandshakeFailsSSL(_FakeSMTPSSL):
        def __init__(
            self,
            host: str,
            port: int,
            timeout: float | None = None,
            context: ssl.SSLContext | None = None,
        ) -> None:
            raise ssl.SSLCertVerificationError("certificate verify failed")

    monkeypatch.setattr(smtplib, "SMTP_SSL", _HandshakeFailsSSL)
    mailer = otp_mailer.OtpMailer(
        _Store("pw"),
        _settings(auth_email_tls_mode="implicit", auth_email_smtp_port=465),
    )
    with pytest.raises(otp_mailer.OtpMailPreflightError) as caught:
        mailer.send_preflight(to="admin@acme.io")
    assert caught.value.detail["stage"] == "connect"
    assert caught.value.detail["error_type"] == "SSLCertVerificationError"


def test_starttls_mode_is_still_the_default_and_still_uses_plain_smtp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: nothing about #1146 may change the default transport. Also
    proves `smtplib.SMTP_SSL` is never even touched on the default path."""
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTPSSL)
    mailer = _mailer(monkeypatch, _Store("pw"))  # auth_email_tls_mode unset → default
    mailer.send_code(to="ada@acme.io", code="123456", expires_in_minutes=10)

    assert _FakeSMTP.instances[-1].calls == ["starttls", "login", "send", "close"]
    assert _FakeSMTPSSL.ssl_instances == []


def test_a_ca_bundle_reaches_the_real_ssl_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The right layer to monkeypatch here is `smtplib.SMTP` (the transport) —
    NOT `ssl.create_default_context`, which is the seam under test. A real
    self-signed PEM is loaded through the real stdlib call, and its presence is
    asserted via `SSLContext.get_ca_certs()`, which reports ONLY certs loaded
    through `load_verify_locations` (i.e. via `cafile=`) — never the ambient
    system store, so a non-empty result is real proof the path was wired
    through, not a coincidence of the default trust store."""
    bundle = tmp_path / "private-ca.pem"
    _write_self_signed_ca(bundle, common_name="dataq-test-ca")
    mailer = _mailer(monkeypatch, _Store("pw"), auth_email_ca_bundle=str(bundle))
    mailer.send_code(to="ada@acme.io", code="123456", expires_in_minutes=10)

    context = _FakeSMTP.instances[-1].tls_context
    assert isinstance(context, ssl.SSLContext)
    ca_certs = context.get_ca_certs()
    assert len(ca_certs) == 1
    # `get_ca_certs()`'s dict shape is loosely typed upstream; a substring check
    # on its repr is a stable, low-friction way to pin the identity without
    # fighting typeshed over the nested-tuple RDN structure.
    assert "dataq-test-ca" in repr(ca_certs[0]["subject"])


def test_no_ca_bundle_configured_uses_the_ordinary_system_default_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset `AUTH_EMAIL_CA_BUNDLE` must build the exact same context shape as
    before #1146: `cafile=None` falls through to `create_default_context`'s own
    `load_default_certs()` branch. Compared against a real, unpatched reference
    context built the same way (not asserted empty — on this platform
    `get_ca_certs()` also reports certs loaded via `load_default_certs()`, so
    the honest regression check is "matches the ordinary default", not "empty").
    """
    reference = ssl.create_default_context()
    mailer = _mailer(monkeypatch, _Store("pw"))
    mailer.send_code(to="ada@acme.io", code="123456", expires_in_minutes=10)
    context = _FakeSMTP.instances[-1].tls_context
    assert isinstance(context, ssl.SSLContext)
    assert len(context.get_ca_certs()) == len(reference.get_ca_certs())


def test_none_mode_never_calls_starttls_and_warns_loudly_every_send(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    """`none` is a deliberate plaintext downgrade — test-only. There is no
    boot-time signal that distinguishes "about to hit a real relay" from "a
    test harness", so the warning fires on every send rather than once."""
    from backend.app.core.logging import configure_logging

    configure_logging()
    mailer = _mailer(monkeypatch, _Store("pw"), auth_email_tls_mode="none")
    mailer.send_code(to="ada@acme.io", code="123456", expires_in_minutes=10)

    smtp = _FakeSMTP.instances[-1]
    assert smtp.calls == ["login", "send", "close"]  # no starttls at all
    assert smtp.tls_context is None  # starttls() was never called to set it

    captured = capsys.readouterr()
    emitted = captured.out + captured.err
    assert "otp_smtp_tls_disabled" in emitted


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


# ── SMTP pre-flight test (#737, ADR 0032 decision 7) ────────────────────────────
#
# `send_preflight` shares `_deliver` with `send_code` (the SAME connect/TLS/login/
# send ladder — the whole point is that a green pre-flight is evidence the
# sign-in path works too), but reports WHICH stage failed instead of collapsing
# every failure into one generic 502. These tests pin the classification.


def test_preflight_sends_a_real_message_over_starttls_before_authenticating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mailer = _mailer(monkeypatch, _Store("app-password"))
    mailer.send_preflight(to="admin@acme.io")

    smtp = _FakeSMTP.instances[-1]
    assert smtp.calls == ["starttls", "login", "send", "close"]
    assert smtp.credentials == ("dataq@example.com", "app-password")
    message = smtp.messages[0]
    assert message["To"] == "admin@acme.io"
    assert "test" in message.get_content().lower()


def test_preflight_classifies_a_bad_host_as_the_connect_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bad host / refused connection / DNS failure all surface before there is a
    context-managed SMTP object — smtplib.SMTP.__init__ connects immediately."""

    class _UnreachableSMTP(_FakeSMTP):
        def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
            raise OSError("[Errno -2] Name or service not known")

    monkeypatch.setattr(smtplib, "SMTP", _UnreachableSMTP)
    mailer = otp_mailer.OtpMailer(_Store("pw"), _settings())

    with pytest.raises(otp_mailer.OtpMailPreflightError) as caught:
        mailer.send_preflight(to="admin@acme.io")
    assert caught.value.status_code == 502
    assert caught.value.code == "otp_email_preflight_failed"
    assert caught.value.detail["stage"] == "connect"
    assert caught.value.detail["error_type"] == "OSError"


def test_preflight_classifies_a_connect_timeout_as_the_connect_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _TimingOutSMTP(_FakeSMTP):
        def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
            raise TimeoutError("timed out")

    monkeypatch.setattr(smtplib, "SMTP", _TimingOutSMTP)
    mailer = otp_mailer.OtpMailer(_Store("pw"), _settings())

    with pytest.raises(otp_mailer.OtpMailPreflightError) as caught:
        mailer.send_preflight(to="admin@acme.io")
    assert caught.value.detail["stage"] == "connect"
    assert caught.value.detail["error_type"] == "TimeoutError"


def test_preflight_classifies_a_handshake_failure_as_the_tls_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _NoTlsSMTP(_FakeSMTP):
        def starttls(self, context: ssl.SSLContext | None = None) -> None:
            raise ssl.SSLError("handshake failed")

    monkeypatch.setattr(smtplib, "SMTP", _NoTlsSMTP)
    mailer = otp_mailer.OtpMailer(_Store("pw"), _settings())

    with pytest.raises(otp_mailer.OtpMailPreflightError) as caught:
        mailer.send_preflight(to="admin@acme.io")
    assert caught.value.detail["stage"] == "tls"
    assert caught.value.detail["error_type"] == "SSLError"


def test_preflight_classifies_a_refused_login_as_the_auth_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _RefusedLoginSMTP(_FakeSMTP):
        def login(self, user: str, password: str) -> None:
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setattr(smtplib, "SMTP", _RefusedLoginSMTP)
    mailer = otp_mailer.OtpMailer(_Store("pw"), _settings())

    with pytest.raises(otp_mailer.OtpMailPreflightError) as caught:
        mailer.send_preflight(to="admin@acme.io")
    assert caught.value.detail["stage"] == "auth"
    assert caught.value.detail["error_type"] == "SMTPAuthenticationError"


def test_preflight_classifies_a_rejected_message_as_the_send_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _RejectedSendSMTP(_FakeSMTP):
        def send_message(self, message: EmailMessage) -> None:
            raise smtplib.SMTPDataError(550, b"message rejected")

    monkeypatch.setattr(smtplib, "SMTP", _RejectedSendSMTP)
    mailer = otp_mailer.OtpMailer(_Store("pw"), _settings())

    with pytest.raises(otp_mailer.OtpMailPreflightError) as caught:
        mailer.send_preflight(to="admin@acme.io")
    assert caught.value.detail["stage"] == "send"
    assert caught.value.detail["error_type"] == "SMTPDataError"


# ── A QUIT-time failure must never replace an in-flight stage error ─────────
#
# Real `smtplib.SMTP.__exit__` sends QUIT and raises `SMTPResponseException` on
# any non-221 reply (only `SMTPServerDisconnected` is swallowed). A `with`
# block that raises while already unwinding another exception has the NEW one
# supersede the original as what actually propagates — so a `with smtp as
# server:` shape would let a QUIT hiccup erase an already-classified auth/tls/
# send failure and escape as an unclassified raw 500 instead of the contracted
# 502 (#737 review). `_deliver` closes explicitly via `.quit()` in a `finally`
# specifically to rule this out; these tests pin that a broken `quit()` never
# changes the outcome, in either direction.


def test_a_quit_failure_never_replaces_an_in_flight_stage_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _QuitFailsAfterAuthFailureSMTP(_FakeSMTP):
        def login(self, user: str, password: str) -> None:
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

        def quit(self) -> None:
            raise smtplib.SMTPResponseException(451, b"garbage QUIT reply")

    monkeypatch.setattr(smtplib, "SMTP", _QuitFailsAfterAuthFailureSMTP)
    mailer = otp_mailer.OtpMailer(_Store("pw"), _settings())

    with pytest.raises(otp_mailer.OtpMailPreflightError) as caught:
        mailer.send_preflight(to="admin@acme.io")
    # The ORIGINAL failure (auth), not the QUIT-time exception, must be what
    # the caller sees.
    assert caught.value.detail["stage"] == "auth"
    assert caught.value.detail["error_type"] == "SMTPAuthenticationError"


def test_a_quit_failure_after_a_clean_send_does_not_manufacture_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The message was already sent successfully — a QUIT-reply hiccup during
    cleanup must not turn that real success into a spurious failure."""

    class _QuitFailsAfterCleanSendSMTP(_FakeSMTP):
        def quit(self) -> None:
            raise smtplib.SMTPResponseException(451, b"garbage QUIT reply")

    monkeypatch.setattr(smtplib, "SMTP", _QuitFailsAfterCleanSendSMTP)
    mailer = otp_mailer.OtpMailer(_Store("pw"), _settings())

    mailer.send_preflight(to="admin@acme.io")  # must not raise
    assert _FakeSMTP.instances[-1].calls == ["starttls", "login", "send"]


def test_send_code_also_keeps_its_uniform_502_through_a_quit_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same property, the OTHER caller: `send_code`'s uniform 502 must survive a
    QUIT-time exception exactly like `send_preflight`'s stage typing does."""

    class _QuitFailsAfterSendFailureSMTP(_FakeSMTP):
        def send_message(self, message: EmailMessage) -> None:
            raise smtplib.SMTPDataError(550, b"message rejected")

        def quit(self) -> None:
            raise smtplib.SMTPResponseException(451, b"garbage QUIT reply")

    monkeypatch.setattr(smtplib, "SMTP", _QuitFailsAfterSendFailureSMTP)
    mailer = otp_mailer.OtpMailer(_Store("pw"), _settings())

    with pytest.raises(otp_mailer.OtpMailSendError) as caught:
        mailer.send_code(to="ada@acme.io", code="123456", expires_in_minutes=10)
    assert caught.value.status_code == 502
    assert caught.value.code == "otp_email_send_failed"


def test_preflight_not_configured_is_distinct_from_a_stage_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mailer block being unset/incomplete must not be mistaken for a
    transport-stage failure — it's a 503 config error, never a 502 with a stage,
    and it must never reach smtplib at all."""
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    broken = Settings.model_construct(
        auth_email_smtp_host=None,
        auth_email_username=None,
        auth_email_from=None,
        auth_email_password_secret_name=None,
        auth_email_smtp_port=587,
        auth_email_timeout_seconds=5.0,
    )
    with pytest.raises(otp_mailer.OtpMailNotConfiguredError) as caught:
        otp_mailer.OtpMailer(_Store("pw"), broken).send_preflight(to="admin@acme.io")
    assert caught.value.status_code == 503
    assert _FakeSMTP.instances == []


def test_preflight_store_unavailable_is_distinct_from_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    sealed = otp_mailer.OtpMailer(_Store(SecretStoreUnavailableError("sealed")), _settings())
    with pytest.raises(otp_mailer.OtpMailStoreUnavailableError) as caught:
        sealed.send_preflight(to="admin@acme.io")
    assert caught.value.code == "secret_store_unavailable"
    assert _FakeSMTP.instances == []


def test_a_preflight_failure_logs_the_stage_and_error_type_but_never_the_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pipeline under test is the REAL logging stack (`configure_logging` +
    the redacting stdout handler), not the `_redact_pii` helper in isolation —
    the failure this guards against is a dependency or a future call site
    logging the password directly, which a helper-level unit test could never
    catch (CLAUDE.md §10: redact at the logger, not the call site).

    A refused-login failure is the adversarial case on purpose: `server.login`
    is called with the real password in scope, one stack frame above the log
    line, so if redaction happened anywhere OTHER than the logger this is where
    it would leak.
    """
    import io
    import logging

    from backend.app.core.logging import configure_logging

    real_password = "s3cr3t-app-password-do-not-leak"

    class _RefusedLoginSMTP(_FakeSMTP):
        def login(self, user: str, password: str) -> None:
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setattr(smtplib, "SMTP", _RefusedLoginSMTP)
    configure_logging()
    buffer = io.StringIO()
    handler = logging.getLogger().handlers[0]
    original = handler.stream  # type: ignore[attr-defined]
    handler.stream = buffer  # type: ignore[attr-defined]
    try:
        with pytest.raises(otp_mailer.OtpMailPreflightError):
            otp_mailer.OtpMailer(_Store(real_password), _settings()).send_preflight(
                to="admin@acme.io"
            )
    finally:
        handler.stream = original  # type: ignore[attr-defined]

    emitted = buffer.getvalue()
    assert "otp_email_preflight_failed" in emitted
    assert '"stage": "auth"' in emitted
    assert "SMTPAuthenticationError" in emitted, "the operator cannot tell what broke"
    assert real_password not in emitted
    assert "admin@acme.io" not in emitted
