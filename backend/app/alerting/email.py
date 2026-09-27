"""Email (SMTP) ``ResultPublisher`` — sends a run's report as an email."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from sqlalchemy.orm import Session

from backend.app.alerting import render
from backend.app.alerting.base import (
    CheckReport,
    ConnectionHealthReport,
    IncidentCard,
    PollStalenessReport,
    RunReport,
)
from backend.app.alerting.routing import route_for
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretNotFoundError, SecretStore
from backend.app.services import channel_service, notification_service

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


def _intro(report: RunReport) -> str:
    target = f" ({report.target_label})" if report.target_label else ""
    subject = f"{report.suite_name}{target}"
    if report.run_status == "failed":
        return (
            f"DataQ couldn't finish checking {subject}, so this data wasn't verified. "
            "Open the run for the reason."
        )
    problems = sum(1 for c in report.checks if c.status not in ("pass", "skip", "error"))
    unverified = sum(1 for c in report.checks if c.status in ("skip", "error"))
    if not problems and not unverified:
        return f"DataQ checked {subject} and every check passed."
    parts = []
    if problems:
        parts.append(
            f"found {problems} problem{'s' if problems != 1 else ''} that "
            f"{'needs' if problems == 1 else 'need'} attention"
        )
    if unverified:
        parts.append(f"couldn't verify {unverified} check{'s' if unverified != 1 else ''}")
    return f"DataQ checked {subject} and " + " and ".join(parts) + "."


def _pair_incidents(
    report: RunReport, shown: list[CheckReport]
) -> tuple[dict[int, IncidentCard], list[IncidentCard]]:
    """Pair each rendered check with its incident — by check id when the report
    carries one, else by name — using each incident at most once, so duplicate
    names can't show one incident twice and hide another. Anything left over is
    returned to be listed on its own, never dropped.
    """
    remaining = list(report.incidents)
    paired: dict[int, IncidentCard] = {}
    for index, check in enumerate(shown):
        if check.check_id is not None:
            match = next((i for i in remaining if i.check_id == check.check_id), None)
        else:
            match = next((i for i in remaining if i.check_name == check.check_name), None)
        if match is not None:
            paired[index] = match
            remaining.remove(match)
    return paired, remaining


def _failing(report: RunReport) -> list[CheckReport]:
    return [c for c in report.checks if c.status != "pass"]


def render_text_body(report: RunReport) -> str:
    """Plain-text body (the alternative for non-HTML clients)."""
    lines = [render_subject(report), "", _intro(report), ""]
    failing = _failing(report)
    shown = failing[:_MAX_CHECK_LINES]
    paired, others = _pair_incidents(report, shown)
    for index, check in enumerate(shown):
        lines.append(f"* {check.check_name} [{_STATUS_LABEL.get(check.status, check.status)}]")
        summary = render.plain_check_summary(check)
        if summary:
            lines.append(f"  What we found: {summary}")
        examples = render.check_sample_values(check)
        if examples:
            lines.append(f"  Examples: {examples.removeprefix('e.g. ')}")
        incident = paired.get(index)
        if incident is not None:
            lines.extend(f"  {label}: {text}" for label, text in render.incident_facts(incident))
        detail = render.check_detail(check)
        if detail:
            lines.append(f"  Technical details: {detail}")
        lines.append("")
    if len(failing) > _MAX_CHECK_LINES:
        lines.append(f"…and {len(failing) - _MAX_CHECK_LINES} more — see the run for all of them.")
    if others:
        lines.append("Other open incidents:")
        for card in others:
            lines.append(f"* {card.check_name}")
            lines.extend(f"  {label}: {text}" for label, text in render.incident_facts(card))
        lines.append("")
    lines += [
        "Run details",
        f"Suite:       {report.suite_name}",
        f"Datasource:  {report.datasource_type}",
        f"Target:      {report.target_label}",
        f"Run status:  {report.run_status}",
        f"Worst severity: {report.worst_severity or '—'}",
    ]
    lines.extend(f"{label}: {value}" for label, value in render.run_metadata(report))
    if report.run_url:
        lines.append(f"View run: {report.run_url}")
    return "\n".join(lines)


_STATUS_LABEL = {
    "critical": "Critical",
    "fail": "Failed",
    "warn": "Warning",
    "error": "Couldn't run",
    "skip": "Skipped",
}
_STATUS_COLOUR = {
    "critical": "#991b1b",
    "fail": "#dc2626",
    "warn": "#d97706",
    "error": "#6b7280",
    "skip": "#6b7280",
}


def _check_block(check: CheckReport, incident: IncidentCard | None) -> str:
    colour = _STATUS_COLOUR.get(check.status, "#dc2626")
    badge = (
        f"<span style='display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;"
        f"color:#fff;background:{colour};margin-left:8px;'>"
        f"{_esc(_STATUS_LABEL.get(check.status, check.status))}</span>"
    )
    parts = [
        f"<div style='font-size:15px;font-weight:600;color:#111827;'>{_esc(check.check_name)}"
        f"{badge}</div>"
    ]
    summary = render.plain_check_summary(check)
    if summary:
        parts.append(f"<p style='margin:6px 0 0;font-size:14px;color:#111827;'>{_esc(summary)}</p>")
    facts: list[tuple[str, str]] = []
    examples = render.check_sample_values(check)
    if examples:
        facts.append(("Examples", examples.removeprefix("e.g. ")))
    if incident is not None:
        facts.extend(render.incident_facts(incident))
    if facts:
        parts.append(
            "<table style='border-collapse:collapse;margin-top:6px;'>"
            + "".join(
                f"<tr><td style='padding:3px 12px 3px 0;font-size:13px;color:#6b7280;"
                f"white-space:nowrap;vertical-align:top;'>{_esc(label)}</td>"
                f"<td style='padding:3px 0;font-size:13px;color:#374151;'>{_esc(text)}</td></tr>"
                for label, text in facts
            )
            + "</table>"
        )
    detail = render.check_detail(check)
    if detail:
        parts.append(
            f"<p style='margin:6px 0 0;font-size:11px;color:#9ca3af;'>Technical details: "
            f"{_esc(detail)}</p>"
        )
    return (
        f"<div style='border-left:3px solid {colour};padding:8px 12px;margin:12px 0;"
        f"background:#fafafa;'>{''.join(parts)}</div>"
    )


def render_html_body(report: RunReport) -> str:
    """Minimal HTML body (inline-styled, email-client safe)."""
    colour = "#16a34a" if report.success else "#dc2626"
    td = _TD
    label_td = f"{td}color:#6b7280;white-space:nowrap;"

    failing = _failing(report)
    shown = failing[:_MAX_CHECK_LINES]
    paired, others = _pair_incidents(report, shown)
    blocks = "".join(_check_block(c, paired.get(i)) for i, c in enumerate(shown))
    if len(failing) > _MAX_CHECK_LINES:
        blocks += (
            f"<p style='font-size:13px;color:#6b7280;'>…and {len(failing) - _MAX_CHECK_LINES} "
            "more — see the run for all of them.</p>"
        )
    if others:
        blocks += "<h3 style='margin:16px 0 0;font-size:15px;'>Other open incidents</h3>" + "".join(
            "<div style='border-left:3px solid #9ca3af;padding:8px 12px;margin:12px 0;"
            "background:#fafafa;'>"
            f"<div style='font-size:15px;font-weight:600;'>{_esc(card.check_name)}</div>"
            + "".join(
                f"<p style='margin:3px 0;font-size:13px;color:#374151;'>"
                f"<span style='color:#6b7280;'>{_esc(label)}:</span> {_esc(text)}</p>"
                for label, text in render.incident_facts(card)
            )
            + "</div>"
            for card in others
        )
    checks_section = (
        f"<h3 style='margin:16px 0 0;font-size:15px;'>What needs attention</h3>{blocks}"
        if blocks
        else ""
    )
    button = (
        f"<p style='margin:16px 0;'><a href='{_esc(report.run_url)}' "
        f"style='display:inline-block;padding:8px 14px;background:#2563eb;color:#fff;"
        f"border-radius:6px;text-decoration:none;font-size:14px;'>View full run →</a></p>"
        if report.run_url
        else ""
    )
    # Run-details table: base facts + shared run metadata (owner/env/trigger/…).
    detail_rows: list[tuple[str, str]] = [
        ("Suite", report.suite_name),
        ("Datasource", report.datasource_type or "—"),
        ("Target", report.target_label),
        ("Severity", report.worst_severity or "—"),
        *render.run_metadata(report),
    ]
    details = (
        "<h3 style='margin:16px 0 0;font-size:13px;color:#6b7280;'>Run details</h3>"
        "<table style='border-collapse:collapse;margin-top:4px;'>"
        + "".join(
            f"<tr><td style='{label_td}'><b>{_esc(k)}</b></td>"
            f"<td style='{td}'>{_esc(v)}</td></tr>"
            for k, v in detail_rows
        )
        + "</table>"
    )
    return (
        f"<div style='font-family:system-ui,Arial,sans-serif;max-width:680px;'>"
        f"<h2 style='color:{colour};margin:0 0 4px;'>{_esc(render_subject(report))}</h2>"
        f"<p style='margin:0 0 8px;font-size:14px;color:#374151;'>{_esc(_intro(report))}</p>"
        f"{checks_section}{button}{details}</div>"
    )


def render_health_subject(report: ConnectionHealthReport) -> str:
    """Subject line for a connection poll-health edge (#837)."""
    return f"[DataQ] {render.health_headline(report).removeprefix('DataQ — ')}"


def render_health_text_body(report: ConnectionHealthReport) -> str:
    """Plain-text body for a connection poll-health edge."""
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


def render_staleness_subject(report: PollStalenessReport) -> str:
    """Subject line for the workspace poll-staleness edge (#1052)."""
    return f"[DataQ] {render.staleness_headline(report).removeprefix('DataQ — ')}"


def render_staleness_text_body(report: PollStalenessReport) -> str:
    """Plain-text body for the workspace poll-staleness edge."""
    lines = [render.staleness_headline(report), ""]
    lines.extend(f"{label}: {value}" for label, value in render.staleness_facts(report))
    lines += ["", render.staleness_impact(report)]
    return "\n".join(lines)


def render_staleness_html_body(report: PollStalenessReport) -> str:
    """Minimal HTML body for the workspace poll-staleness edge (same table style)."""
    colour = "#dc2626" if report.is_failing else "#16a34a"
    rows = "".join(
        f"<tr><td style='{_TD};font-weight:600;'>{_esc(label)}</td>"
        f"<td style='{_TD}'>{_esc(value)}</td></tr>"
        for label, value in render.staleness_facts(report)
    )
    return (
        f"<div style='font-family:system-ui,Arial,sans-serif;'>"
        f"<h2 style='color:{colour};margin:0 0 4px;'>{_esc(render.staleness_headline(report))}</h2>"
        f"<p style='margin:0 0 12px;color:#4b5563;'>{_esc(render.staleness_impact(report))}</p>"
        f"<table style='border-collapse:collapse;'>{rows}</table></div>"
    )


def _esc(value: str) -> str:
    """Minimal HTML escaping for interpolated text."""
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


class EmailPublisher:
    """Sends a run's report over SMTP (STARTTLS) to the per-suite recipients, else
    the workspace ``EMAIL_TO`` (#633). The SMTP transport (host/port/credentials/
    sender) is workspace-level and mandatory; only the recipient list is per-suite.
    """

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
        """Send the email per the run's suite notification policy."""
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
        primary = notification_service.resolve_email_recipients(
            config, workspace_recipients=self._recipients
        )
        channel_recipients = channel_service.resolve_channel_email_recipients(
            session, report.suite_id
        )
        recipients = tuple(dict.fromkeys([*primary, *channel_recipients]))
        if not recipients:
            return  # no per-suite override, no linked email channel, no workspace EMAIL_TO
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

    def publish_health(self, session: Session, report: ConnectionHealthReport) -> bool:
        """Email a connection poll-health edge to the **workspace** recipients (#837)."""
        if not (self._username and self._password_secret_name and self._sender):
            return False
        if not self._recipients:
            return False
        try:
            password = self._secret_store.get(self._password_secret_name)
        except SecretNotFoundError:
            log.warning("email_password_unresolved", secret_name=self._password_secret_name)
            return False
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
        return True

    def publish_poll_staleness(self, session: Session, report: PollStalenessReport) -> bool:
        """Email the workspace poll-staleness edge (#1052) to the workspace
        recipients — same transport gating as :meth:`publish_health`, but returning
        whether a message was actually sent (``False`` on any quiet-skip gate,
        including an unresolvable password — nothing left this process).
        """
        if not (self._username and self._password_secret_name and self._sender):
            return False
        if not self._recipients:
            return False
        try:
            password = self._secret_store.get(self._password_secret_name)
        except SecretNotFoundError:
            log.warning("email_password_unresolved", secret_name=self._password_secret_name)
            return False
        message = self._message(
            subject=render_staleness_subject(report),
            recipients=self._recipients,
            text=render_staleness_text_body(report),
            html=render_staleness_html_body(report),
        )
        self._send(message, password=password)
        log.info(
            "email_staleness_alert_sent",
            state=report.state,
            recipients=len(self._recipients),
        )
        return True

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
        property that must not be able to differ between two copies of it.
        """
        context = ssl.create_default_context()
        with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=self._timeout) as server:
            server.starttls(context=context)
            # mypy: guarded by the transport check in both callers.
            server.login(self._username or "", password)
            server.send_message(message)
