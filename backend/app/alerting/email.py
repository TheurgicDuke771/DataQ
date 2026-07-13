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
from backend.app.alerting.base import CheckReport, ConnectionHealthReport, RunReport
from backend.app.alerting.routing import route_for
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretNotFoundError, SecretStore
from backend.app.services import notification_service

log = get_logger(__name__)

_SMTP_TIMEOUT_SECONDS = 15.0
_MAX_CHECK_LINES = 20

# Inline table-cell style, shared by the run + connection-health bodies (email clients
# strip <style> blocks, so it has to be inline on every cell).
_TD = "padding:6px 10px;border-bottom:1px solid #eee;font-size:13px;vertical-align:top;"


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
    # Minimal incident references (ADR 0034 #761; rich formatting defers to #773).
    if report.incidents:
        lines.append("Incidents:")
        lines.extend(f"  - {render.incident_line(card)}" for card in report.incidents)
    return "\n".join(lines)


def render_html_body(report: RunReport) -> str:
    """Minimal HTML body (inline-styled, email-client safe).

    Two tables: a **run details** key/value table (suite, owner, datasource,
    target, severity + the shared run metadata) and a **failing-checks** table
    (Status · Check · Details). Everything the alert carries is tabular (#661)."""
    colour = "#16a34a" if report.success else "#dc2626"
    th = (
        "padding:6px 10px;text-align:left;border-bottom:2px solid #d1d5db;"
        "font-size:13px;color:#374151;"
    )
    td = _TD
    label_td = f"{td}color:#6b7280;white-space:nowrap;"

    # Run-details table: base facts + shared run metadata (owner/env/trigger/…),
    # dropping any metadata value that isn't set.
    detail_rows: list[tuple[str, str]] = [
        ("Suite", report.suite_name),
        ("Datasource", report.datasource_type or "—"),
        ("Target", report.target_label),
        ("Severity", report.worst_severity or "—"),
        *render.run_metadata(report),
    ]
    details = (
        "<table style='border-collapse:collapse;margin-top:12px;'>"
        + "".join(
            f"<tr><td style='{label_td}'><b>{_esc(k)}</b></td>"
            f"<td style='{td}'>{_esc(v)}</td></tr>"
            for k, v in detail_rows
        )
        + "</table>"
    )

    # Failing-checks table with a header row (Status · Check · Details).
    rows = "".join(
        f"<tr><td style='{td}'><code>{_esc(c.status)}</code></td>"
        f"<td style='{td}'>{_esc(c.check_name)}</td>"
        f"<td style='{td}color:#6b7280;'>{_esc(render.check_detail(c))}</td></tr>"
        for c in report.checks
        if c.status != "pass"
    )
    checks_table = (
        f"<h3 style='margin:16px 0 0;font-size:14px;'>Failing checks</h3>"
        f"<table style='border-collapse:collapse;margin-top:6px;'>"
        f"<thead><tr><th style='{th}'>Status</th><th style='{th}'>Check</th>"
        f"<th style='{th}'>Details</th></tr></thead><tbody>{rows}</tbody></table>"
        if rows
        else ""
    )
    # Minimal incident references (ADR 0034 #761; rich formatting defers to #773).
    incidents_block = (
        "<p style='margin:12px 0 0;font-size:13px;color:#6b7280;'>"
        + "<br/>".join(_esc(render.incident_line(card)) for card in report.incidents)
        + "</p>"
        if report.incidents
        else ""
    )
    button = (
        f"<p style='margin:16px 0 0;'><a href='{_esc(report.run_url)}' "
        f"style='color:#2563eb;'>View run →</a></p>"
        if report.run_url
        else ""
    )
    return (
        f"<div style='font-family:system-ui,Arial,sans-serif;'>"
        f"<h2 style='color:{colour};margin:0 0 4px;'>{_esc(render_subject(report))}</h2>"
        f"{details}{checks_table}{incidents_block}{button}</div>"
    )


def render_health_subject(report: ConnectionHealthReport) -> str:
    """Subject line for a connection poll-health edge (#837)."""
    return f"[DataQ] {render.health_headline(report).removeprefix('DataQ — ')}"


def render_health_text_body(report: ConnectionHealthReport) -> str:
    """Plain-text body for a connection poll-health edge.

    Reads only the report's **classified** ``reason``; the raw exception (which can
    carry a SAS/DSN/token) never reaches the mailer.
    """
    lines = [render.health_headline(report), ""]
    lines.extend(f"{label}: {value}" for label, value in render.health_facts(report))
    lines += ["", render.health_impact(report)]
    if report.connection_url:
        lines.append(f"View connection: {report.connection_url}")
    return "\n".join(lines)


def render_health_html_body(report: ConnectionHealthReport) -> str:
    """Minimal HTML body for a connection poll-health edge (same table style as a run)."""
    colour = "#dc2626" if report.is_failing else "#16a34a"
    rows = "".join(
        f"<tr><td style='{_TD};font-weight:600;'>{_esc(label)}</td>"
        f"<td style='{_TD}'>{_esc(value)}</td></tr>"
        for label, value in render.health_facts(report)
    )
    button = (
        f"<p style='margin:16px 0 0;'><a href='{_esc(report.connection_url)}' "
        f"style='color:#2563eb;'>View connection →</a></p>"
        if report.connection_url
        else ""
    )
    return (
        f"<div style='font-family:system-ui,Arial,sans-serif;'>"
        f"<h2 style='color:{colour};margin:0 0 4px;'>{_esc(render.health_headline(report))}</h2>"
        f"<p style='margin:0 0 12px;color:#4b5563;'>{_esc(render.health_impact(report))}</p>"
        f"<table style='border-collapse:collapse;'>{rows}</table>{button}</div>"
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

        message = self._message(
            subject=render_subject(report),
            recipients=recipients,
            text=render_text_body(report),
            html=render_html_body(report),
        )
        self._send(message, password=password)
        log.info(
            "email_alert_sent",
            run_id=str(report.run_id),
            suite=report.suite_name,
            recipients=len(recipients),
            worst_severity=report.worst_severity,
        )

    def publish_health(self, session: Session, report: ConnectionHealthReport) -> None:
        """Email a connection poll-health edge to the **workspace** recipients (#837).

        A connection has no suite, so there is no per-suite recipient override to
        resolve — this goes to ``EMAIL_TO``. Quiet no-op when the SMTP transport or the
        workspace recipient list is unconfigured.
        """
        if not (self._username and self._password_secret_name and self._sender):
            return
        if not self._recipients:
            return
        try:
            password = self._secret_store.get(self._password_secret_name)
        except SecretNotFoundError:
            log.warning("email_password_unresolved", secret_name=self._password_secret_name)
            return
        message = self._message(
            subject=render_health_subject(report),
            recipients=self._recipients,
            text=render_health_text_body(report),
            html=render_health_html_body(report),
        )
        self._send(message, password=password)
        log.info(
            "email_health_alert_sent",
            connection_id=str(report.connection_id),
            state=report.state,
            recipients=len(self._recipients),
        )

    def _message(
        self, *, subject: str, recipients: tuple[str, ...], text: str, html: str
    ) -> EmailMessage:
        """A multipart text+HTML message from this publisher's sender to ``recipients``."""
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self._sender
        message["To"] = ", ".join(recipients)
        message.set_content(text)
        message.add_alternative(html, subtype="html")
        return message

    def _send(self, message: EmailMessage, *, password: str) -> None:
        """SMTP submission over STARTTLS. Shared by the run + health paths so the
        transport (and its TLS context) is implemented exactly once — a security
        property that must not be able to differ between two copies of it."""
        context = ssl.create_default_context()
        with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=self._timeout) as server:
            server.starttls(context=context)
            # mypy: guarded by the transport check in both callers.
            server.login(self._username or "", password)
            server.send_message(message)
