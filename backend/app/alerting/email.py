"""Email (SMTP) ``ResultPublisher`` — sends a run's report as an email.

Workspace-level: one sender (SMTP submission with STARTTLS) and a fixed
recipient list. The password (e.g. a Gmail app-password) is resolved from the
SecretStore by name; the rest of the SMTP coordinates are non-secret config.
Delivery follows the **same** per-suite policy as the other publishers (the
suite's `enabled` flag + `alert_on` threshold via :func:`routing.route_for`);
only the rendering + transport differ. Unconfigured (no recipients / username /
password secret) is a quiet no-op.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from sqlalchemy.orm import Session

from backend.app.alerting import render
from backend.app.alerting.base import CheckReport, RunReport
from backend.app.alerting.routing import route_for
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretNotFoundError, SecretStore
from backend.app.services import notification_service

log = get_logger(__name__)

_SMTP_TIMEOUT_SECONDS = 15.0
_MAX_CHECK_LINES = 20


def render_subject(report: RunReport) -> str:
    """The email subject line — verdict + suite + counts at a glance."""
    if report.success:
        return f"[DataQ] {report.suite_name}: all {report.total_checks} checks passed"
    sev = (report.worst_severity or "fail").upper()
    return (
        f"[DataQ] {sev} — {report.suite_name}: "
        f"{report.failed_checks}/{report.total_checks} checks failed"
    )


def render_text_body(report: RunReport) -> str:
    """Plain-text body (the alternative for non-HTML clients)."""
    lines = [
        render_subject(report),
        "",
        f"Suite:       {report.suite_name}",
        f"Datasource:  {report.datasource_type}",
        f"Target:      {report.target_label}",
        f"Run status:  {report.run_status}",
        f"Worst severity: {report.worst_severity or '—'}",
    ]
    lines.extend(f"{label}: {value}" for label, value in render.run_metadata(report))
    if report.run_url:
        lines.append(f"View run: {report.run_url}")
    lines.append("")
    failing = [c for c in report.checks if c.status != "pass"]
    if failing:
        lines.append("Failing checks:")
        lines.extend(_check_line(c) for c in failing[:_MAX_CHECK_LINES])
        if len(failing) > _MAX_CHECK_LINES:
            lines.append(f"  …and {len(failing) - _MAX_CHECK_LINES} more")
    return "\n".join(lines)


def render_html_body(report: RunReport) -> str:
    """Minimal HTML body (inline-styled, email-client safe)."""
    colour = "#16a34a" if report.success else "#dc2626"
    rows = "".join(
        f"<tr><td style='padding:2px 8px;'><code>{_esc(c.status)}</code></td>"
        f"<td style='padding:2px 8px;'>{_esc(c.check_name)}</td>"
        f"<td style='padding:2px 8px;color:#6b7280;'>{_esc(render.check_detail(c))}</td></tr>"
        for c in report.checks
        if c.status != "pass"
    )
    table = (
        f"<table style='border-collapse:collapse;margin-top:8px;'>{rows}</table>" if rows else ""
    )
    meta = " &nbsp;·&nbsp; ".join(
        f"<b>{_esc(label)}:</b> {_esc(value)}" for label, value in render.run_metadata(report)
    )
    meta_line = f"<p style='margin:6px 0 0;color:#6b7280;'>{meta}</p>" if meta else ""
    button = (
        f"<p style='margin:10px 0 0;'><a href='{_esc(report.run_url)}' "
        f"style='color:#2563eb;'>View run →</a></p>"
        if report.run_url
        else ""
    )
    return (
        f"<div style='font-family:system-ui,Arial,sans-serif;'>"
        f"<h2 style='color:{colour};margin:0 0 8px;'>{_esc(render_subject(report))}</h2>"
        f"<p style='margin:0;color:#374151;'>"
        f"<b>Datasource:</b> {_esc(report.datasource_type)} &nbsp;·&nbsp; "
        f"<b>Target:</b> {_esc(report.target_label)} &nbsp;·&nbsp; "
        f"<b>Severity:</b> {_esc(report.worst_severity or '—')}</p>"
        f"{meta_line}{table}{button}</div>"
    )


def _check_line(check: CheckReport) -> str:
    detail = render.check_detail(check)
    return f"  - [{check.status}] {check.check_name}" + (f" — {detail}" if detail else "")


def _esc(value: str) -> str:
    """Minimal HTML escaping for interpolated text."""
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


class EmailPublisher:
    """Sends a run's report over SMTP (STARTTLS) to the per-suite recipients, else
    the workspace ``EMAIL_TO`` (#633). The SMTP transport (host/port/credentials/
    sender) is workspace-level and mandatory; only the recipient list is per-suite."""

    def __init__(
        self,
        *,
        secret_store: SecretStore,
        smtp_host: str,
        smtp_port: int,
        username: str | None,
        password_secret_name: str | None,
        sender: str | None,
        recipients: tuple[str, ...],
        timeout: float = _SMTP_TIMEOUT_SECONDS,
    ) -> None:
        self._secret_store = secret_store
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._username = username
        self._password_secret_name = password_secret_name
        self._sender = sender or username
        self._recipients = recipients
        self._timeout = timeout

    def publish(self, session: Session, report: RunReport) -> None:
        """Send the email per the run's suite notification policy.

        Quiet no-op when the SMTP transport is unconfigured, no recipients resolve
        (neither per-suite nor workspace), the suite disabled alerting, or the run is
        below the suite's threshold. Raises on an SMTP error — the composite layer
        isolates it so a flaky mailer can't fail the run or block the other channels.
        """
        # The SMTP transport is workspace-level and mandatory (recipients are per-suite).
        if not (self._username and self._password_secret_name and self._sender):
            return
        config = notification_service.get_config(session, report.suite_id)
        if config is not None and not config.enabled:
            return
        policy = config.alert_on if config is not None else notification_service.DEFAULT_ALERT_ON
        route = route_for(report, policy)
        if not route.should_send:
            return
        recipients = notification_service.resolve_email_recipients(
            config, workspace_recipients=self._recipients
        )
        if not recipients:
            return  # no per-suite override and no workspace EMAIL_TO → no-op
        try:
            password = self._secret_store.get(self._password_secret_name)
        except SecretNotFoundError:
            log.warning("email_password_unresolved", secret_name=self._password_secret_name)
            return

        message = EmailMessage()
        message["Subject"] = render_subject(report)
        message["From"] = self._sender
        message["To"] = ", ".join(recipients)
        message.set_content(render_text_body(report))
        message.add_alternative(render_html_body(report), subtype="html")

        context = ssl.create_default_context()
        with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=self._timeout) as server:
            server.starttls(context=context)
            server.login(self._username, password)
            server.send_message(message)
        log.info(
            "email_alert_sent",
            run_id=str(report.run_id),
            suite=report.suite_name,
            recipients=len(recipients),
            worst_severity=report.worst_severity,
        )
