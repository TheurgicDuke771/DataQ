"""The OTP sign-in mailer — ADR 0032 decision 7 (#734).

A deliberately **separate module and config block** from `alerting/email.py`,
reusing its SMTP+STARTTLS transport *shape* but not its error contract. The alert
mailer is best-effort: it no-ops when unconfigured and swallows an unresolvable
password so a flaky channel can never fail a run. This mailer is the opposite —
it sits on the sign-in request path, synchronous, with a short timeout, and every
failure is surfaced to the caller and logged. A misconfigured alert channel must
never block sign-in, and a misconfigured sign-in mailer must never look like a
delivered code.

**Not-configured and unavailable are different errors** (ADR 0039 decision 6):
`SecretNotFoundError` (the password secret was never set) and
`SecretStoreUnavailableError` (the vault is sealed / unreachable) surface as
distinct codes. Folding an outage into "not configured" is the exact defect ADR
0039 records — it makes an operator go fix a setting that was already correct.

The mail is **plain text, code only, no URLs** — ADR 0032 rejected magic links
because corporate mail scanners prefetch URLs (consuming single-use links) and
links leak through referrers and logs. Nothing clickable goes in this message.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.core.secrets import (
    SecretNotFoundError,
    SecretStore,
    SecretStoreUnavailableError,
)

log = get_logger(__name__)


class OtpMailNotConfiguredError(DataQError):
    """The mailer's password secret is not set — an operator error, not a caller's.

    503, and deliberately NOT the same error as an unreachable secret store: the
    fix for this one is "set the secret", and the fix for that one is "bring the
    vault back". Reporting a sealed vault as "not configured" sends the operator
    to change a setting that was already right (ADR 0039 decision 6).
    """

    def __init__(self, secret_name: str) -> None:
        super().__init__(
            "Email sign-in is not fully configured on this deployment: the SMTP "
            "password secret is not set. Contact your workspace administrator.",
            code="otp_email_not_configured",
            status_code=503,
            detail={"secret_name": secret_name},
        )


class OtpMailStoreUnavailableError(DataQError):
    """The secret store could not be reached — a transient outage, so 503 + retry."""

    def __init__(self) -> None:
        super().__init__(
            "Email sign-in is temporarily unavailable: the secret store could not "
            "be reached. Try again shortly.",
            code="secret_store_unavailable",
            status_code=503,
        )


class OtpMailSendError(DataQError):
    """SMTP submission failed — 502, the upstream-gateway class.

    Surfaced, never swallowed: a sign-in flow that reports "check your email"
    when nothing was sent strands the user with no way to tell a slow mail server
    from a dead one (issue #734 AC: "no quiet no-op").
    """

    def __init__(self) -> None:
        super().__init__(
            "Could not send the sign-in code: the mail server rejected the "
            "message or did not respond. Try again shortly.",
            code="otp_email_send_failed",
            status_code=502,
        )


def render_subject() -> str:
    return "Your DataQ sign-in code"


def render_body(code: str, *, expires_in_minutes: int) -> str:
    """Plain text, no URLs (ADR 0032 — codes, not links)."""
    return (
        "Your DataQ sign-in code is:\n\n"
        f"    {code}\n\n"
        f"It expires in {expires_in_minutes} minutes and can be used once.\n\n"
        "If you did not request this code, you can ignore this message — "
        "somebody entered your address on a DataQ sign-in screen, and without "
        "the code they cannot get in.\n"
    )


class OtpMailer:
    """Sends one sign-in code over SMTP+STARTTLS. Stateless per send."""

    def __init__(self, secret_store: SecretStore, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._secret_store = secret_store

    def send_code(self, *, to: str, code: str, expires_in_minutes: int) -> None:
        s = self._settings
        secret_name = s.auth_email_password_secret_name
        if not (s.auth_email_smtp_host and s.auth_email_username and s.auth_email_from):
            # Unreachable behind `otp_auth_configured` + the startup validator; kept
            # as defence-in-depth so a future caller cannot reach smtplib with a
            # half-built transport (and mypy gets its narrowing).
            raise OtpMailNotConfiguredError(secret_name or "AUTH_EMAIL_PASSWORD_SECRET_NAME")
        assert secret_name is not None  # guaranteed by `_validate_otp_auth`
        try:
            password = self._secret_store.get(secret_name)
        except SecretNotFoundError as exc:
            log.error("otp_email_password_unresolved", secret_name=secret_name)
            raise OtpMailNotConfiguredError(secret_name) from exc
        except SecretStoreUnavailableError as exc:
            log.error("otp_email_secret_store_unavailable", secret_name=secret_name)
            raise OtpMailStoreUnavailableError() from exc

        message = EmailMessage()
        message["Subject"] = render_subject()
        message["From"] = s.auth_email_from
        message["To"] = to
        message.set_content(render_body(code, expires_in_minutes=expires_in_minutes))

        context = ssl.create_default_context()
        try:
            with smtplib.SMTP(
                s.auth_email_smtp_host,
                s.auth_email_smtp_port,
                timeout=s.auth_email_timeout_seconds,
            ) as server:
                server.starttls(context=context)
                server.login(s.auth_email_username, password)
                server.send_message(message)
        except Exception as exc:
            # Broad on purpose: smtplib raises OSError/socket.timeout/ssl.SSLError as
            # well as its own SMTPException family, and the caller's contract is a
            # single 502 either way. The exception TYPE is logged so an operator can
            # still tell a TLS failure from a refused login — the message is not,
            # because SMTP servers echo the envelope (i.e. the user's address) in
            # their rejection text, and `_PII_KEYS` cannot redact it out of a bare
            # string that does not look like an address.
            log.error(
                "otp_email_send_failed",
                smtp_host=s.auth_email_smtp_host,
                smtp_port=s.auth_email_smtp_port,
                error_type=type(exc).__name__,
            )
            raise OtpMailSendError() from exc
        log.info("otp_email_sent", smtp_host=s.auth_email_smtp_host)
