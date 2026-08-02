"""The OTP sign-in mailer — ADR 0032 decision 7 (#734), plus the admin SMTP
pre-flight check (#737).

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

**Two callers, one transport (#737).** `send_code` (the sign-in path) and
`send_preflight` (the admin-gated diagnostic, issue #737) both submit over the
same connect → STARTTLS → login → send ladder via the private `_deliver` helper.
They differ only in their error CONTRACT: `send_code` collapses every transport
failure into one uniform 502 (a sign-in requester is not trusted with mail-server
internals), while `send_preflight` reports which STAGE failed — connect / tls /
auth / send — because that is the entire point of an install-time diagnostic
(ADR 0032 decision 7). One classification point, two contracts, instead of two
copies of the same try/except ladder drifting apart.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from typing import Literal

from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.core.secrets import (
    SecretNotFoundError,
    SecretStore,
    SecretStoreUnavailableError,
)

log = get_logger(__name__)

#: The four points along the transport where delivery can fail, in the order
#: they're attempted. Shared vocabulary between `_deliver`'s internal
#: classification and `OtpMailPreflightError`'s public `detail.stage`.
SmtpStage = Literal["connect", "tls", "auth", "send"]


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


class OtpMailPreflightError(DataQError):
    """SMTP pre-flight failed at a specific transport stage (#737).

    Unlike `OtpMailSendError` (the sign-in path's uniform, stage-blind 502 — a
    caller requesting a sign-in code is not trusted with mail-server internals),
    this is reached only from the admin-gated pre-flight endpoint: an operator
    running install-time diagnostics needs to know whether the relay was
    unreachable, TLS negotiation failed, the login was refused, or the message
    itself was rejected (ADR 0032 decision 7 / issue #737 AC 1).

    The exception TYPE is exposed in `detail.error_type`, never its MESSAGE —
    SMTP servers echo the envelope (i.e. the recipient address) into rejection
    text, which `_PII_KEYS` cannot reach inside a bare string (the same
    reasoning `send_code`'s log line already documents).
    """

    def __init__(self, stage: SmtpStage, error_type: str) -> None:
        super().__init__(
            f"SMTP pre-flight failed at the '{stage}' stage — see the server log "
            "for the underlying error type.",
            code="otp_email_preflight_failed",
            status_code=502,
            detail={"stage": stage, "error_type": error_type},
        )


class _SmtpStageError(Exception):
    """Internal only — WHICH transport stage failed. Never crosses the module
    boundary: both public callers catch it and re-raise their own contract (see
    module docstring). Carries the ORIGINAL exception so a caller's log line can
    still report its type."""

    def __init__(self, stage: SmtpStage, original: Exception) -> None:
        super().__init__(str(original))
        self.stage: SmtpStage = stage
        self.original = original


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


def _render_preflight_body() -> str:
    return (
        "This is a test message from DataQ's SMTP pre-flight check "
        "(POST /api/v1/admin/auth-email/test). If you received this, the "
        "email sign-in mailer is configured correctly.\n"
    )


class OtpMailer:
    """Sends mail over SMTP+STARTTLS. Stateless per send."""

    def __init__(self, secret_store: SecretStore, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._secret_store = secret_store

    def _resolve_password(self) -> str:
        """Shared by `send_code` and `send_preflight`: validate the transport
        block is complete, then resolve the password from the secret store —
        the "not configured" vs. "store unavailable" distinction (ADR 0039
        decision 6) applies identically to both callers."""
        s = self._settings
        secret_name = s.auth_email_password_secret_name
        if not (s.auth_email_smtp_host and s.auth_email_username and s.auth_email_from):
            # Unreachable behind `otp_auth_configured` + the startup validator for
            # `send_code`'s sign-in caller; `send_preflight`'s admin caller has no
            # such upstream gate, so this is the FIRST line of defence there, not
            # merely defence-in-depth.
            raise OtpMailNotConfiguredError(secret_name or "AUTH_EMAIL_PASSWORD_SECRET_NAME")
        assert secret_name is not None  # guaranteed by `_validate_otp_auth`
        try:
            return self._secret_store.get(secret_name)
        except SecretNotFoundError as exc:
            log.error("otp_email_password_unresolved", secret_name=secret_name)
            raise OtpMailNotConfiguredError(secret_name) from exc
        except SecretStoreUnavailableError as exc:
            log.error("otp_email_secret_store_unavailable", secret_name=secret_name)
            raise OtpMailStoreUnavailableError() from exc

    def _deliver(self, message: EmailMessage, password: str) -> None:
        """Connect → STARTTLS → login → send, classifying WHICH stage failed.

        Raises `_SmtpStageError` (internal) on any failure — never smtplib's own
        exception types directly — so both callers get a uniform thing to catch.
        The connect/TLS/login/send ladder itself is the ONE copy in the module;
        `send_code` and `send_preflight` differ only in what they do with a
        `_SmtpStageError`.

        Only ever called after `_resolve_password()` has returned successfully,
        which is what actually guarantees the transport block is complete; the
        asserts below just carry that guarantee across the method boundary for
        mypy (narrowing on `s.attr` doesn't survive a call into another method).
        """
        s = self._settings
        assert s.auth_email_smtp_host is not None
        assert s.auth_email_username is not None
        context = ssl.create_default_context()
        try:
            smtp = smtplib.SMTP(
                s.auth_email_smtp_host,
                s.auth_email_smtp_port,
                timeout=s.auth_email_timeout_seconds,
            )
        except Exception as exc:
            # The constructor itself connects (smtplib.SMTP.__init__ calls
            # self.connect() when a host is given) — DNS failure, connection
            # refused, and connect timeout all land here, before there is a
            # context-managed object to enter.
            raise _SmtpStageError("connect", exc) from exc
        with smtp as server:
            try:
                server.starttls(context=context)
            except Exception as exc:
                raise _SmtpStageError("tls", exc) from exc
            try:
                server.login(s.auth_email_username, password)
            except Exception as exc:
                raise _SmtpStageError("auth", exc) from exc
            try:
                server.send_message(message)
            except Exception as exc:
                raise _SmtpStageError("send", exc) from exc

    def send_code(self, *, to: str, code: str, expires_in_minutes: int) -> None:
        s = self._settings
        password = self._resolve_password()

        message = EmailMessage()
        message["Subject"] = render_subject()
        message["From"] = s.auth_email_from
        message["To"] = to
        message.set_content(render_body(code, expires_in_minutes=expires_in_minutes))

        try:
            self._deliver(message, password)
        except _SmtpStageError as exc:
            # Broad classification on purpose: smtplib raises OSError/socket.timeout/
            # ssl.SSLError as well as its own SMTPException family, and the caller's
            # contract is a single 502 either way. The exception TYPE is logged so an
            # operator can still tell a TLS failure from a refused login — the
            # message is not, because SMTP servers echo the envelope (i.e. the
            # user's address) in their rejection text, and `_PII_KEYS` cannot redact
            # it out of a bare string that does not look like an address.
            log.error(
                "otp_email_send_failed",
                smtp_host=s.auth_email_smtp_host,
                smtp_port=s.auth_email_smtp_port,
                error_type=type(exc.original).__name__,
                stage=exc.stage,
            )
            raise OtpMailSendError() from exc.original
        log.info("otp_email_sent", smtp_host=s.auth_email_smtp_host)

    def send_preflight(self, *, to: str) -> None:
        """Admin-gated SMTP diagnostic (#737): send a real test message to the
        CALLER's own address and surface exactly which transport stage failed.

        Reuses `_deliver` — the identical connect/TLS/login/send path
        `send_code` uses — so a green pre-flight is real evidence the sign-in
        path will also work, not a parallel implementation that could drift
        from it. Unlike `send_code`, failures are stage-typed
        (`OtpMailPreflightError`) rather than collapsed to one generic 502: an
        operator running this at install time needs to know WHERE it broke.
        """
        s = self._settings
        password = self._resolve_password()

        message = EmailMessage()
        message["Subject"] = "DataQ SMTP pre-flight test"
        message["From"] = s.auth_email_from
        message["To"] = to
        message.set_content(_render_preflight_body())

        try:
            self._deliver(message, password)
        except _SmtpStageError as exc:
            log.error(
                "otp_email_preflight_failed",
                smtp_host=s.auth_email_smtp_host,
                smtp_port=s.auth_email_smtp_port,
                error_type=type(exc.original).__name__,
                stage=exc.stage,
            )
            raise OtpMailPreflightError(exc.stage, type(exc.original).__name__) from exc.original
        log.info("otp_email_preflight_sent", smtp_host=s.auth_email_smtp_host)
