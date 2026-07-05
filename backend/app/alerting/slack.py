"""Slack ``ResultPublisher`` — posts a run's report to a Slack incoming webhook.

The webhook is resolved **per-suite first, then the workspace one** (#633) — a
suite can override the channel (its own incoming webhook) via its notification
config, falling back to the workspace ``SLACK_WEBHOOK_SECRET_NAME``, exactly like
the Teams publisher. Delivery follows the same per-suite policy — the suite's
`enabled` flag and its `alert_on` threshold via :func:`routing.route_for` — so only
the rendering and destination differ. No webhook resolving (neither per-suite nor
workspace) is a quiet no-op, so the publisher is safe to keep in the registry
composite even when Slack is off.
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from backend.app.alerting.base import CheckReport, RunReport
from backend.app.alerting.routing import CRITICAL, Route, route_for
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.services import notification_service

log = get_logger(__name__)

_POST_TIMEOUT_SECONDS = 10.0
_MAX_CHECK_LINES = 10
# Slack emoji shortcodes per worst severity (clean runs use the check mark).
_SEVERITY_EMOJI = {CRITICAL: ":rotating_light:", "fail": ":x:", "warn": ":warning:"}


def render_slack_message(report: RunReport, route: Route) -> dict[str, object]:
    """The Slack incoming-webhook payload (``text`` + Block Kit ``blocks``).

    ``text`` is the notification fallback/summary; ``blocks`` render the card.
    Pure — boundary DTO in, JSON body out — so it's unit-testable without a send.
    """
    if report.success:
        headline = (
            f":white_check_mark: DataQ — {report.suite_name}: "
            f"all {report.total_checks} checks passed"
        )
    else:
        emoji = _SEVERITY_EMOJI.get(report.worst_severity or "fail", ":x:")
        headline = (
            f"{emoji} DataQ — {report.suite_name}: "
            f"{report.failed_checks}/{report.total_checks} checks failed"
        )

    blocks: list[dict[str, object]] = [
        {"type": "header", "text": {"type": "plain_text", "text": headline[:150]}},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Datasource:*\n{report.datasource_type}"},
                {"type": "mrkdwn", "text": f"*Target:*\n{report.target_label}"},
                {"type": "mrkdwn", "text": f"*Severity:*\n{report.worst_severity or '—'}"},
                {"type": "mrkdwn", "text": f"*Run:*\n{report.run_status}"},
            ],
        },
    ]

    failing = [c for c in report.checks if c.status != "pass"]
    if failing:
        lines = "\n".join(_check_line(c) for c in failing[:_MAX_CHECK_LINES])
        if len(failing) > _MAX_CHECK_LINES:
            lines += f"\n…and {len(failing) - _MAX_CHECK_LINES} more"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": lines}})

    # `<!channel>` escalates a critical breach to everyone in the channel.
    if route.mention_channel:
        blocks.insert(
            0, {"type": "section", "text": {"type": "mrkdwn", "text": "<!channel> *CRITICAL*"}}
        )

    return {"text": headline, "blocks": blocks}


def _check_line(check: CheckReport) -> str:
    """One failing check as a Slack mrkdwn bullet (name · status · redacted sample)."""
    line = f"• *{check.check_name}* — `{check.status}`"
    sample = check.sample_summary or {}
    pct = sample.get("unexpected_percent")
    count = sample.get("unexpected_count")
    if pct is not None:
        line += f" ({pct}% unexpected)"
    elif count is not None:
        line += f" ({count} unexpected)"
    return line


class SlackPublisher:
    """Posts a run's report to a workspace Slack incoming webhook."""

    def __init__(
        self,
        *,
        secret_store: SecretStore,
        webhook_secret_name: str | None,
        allowed_hosts: tuple[str, ...],
        timeout: float = _POST_TIMEOUT_SECONDS,
    ) -> None:
        self._secret_store = secret_store
        self._webhook_secret_name = webhook_secret_name
        self._allowed_hosts = allowed_hosts
        self._timeout = timeout

    def publish(self, session: Session, report: RunReport) -> None:
        """Deliver to Slack per the run's suite notification policy.

        Quiet no-op when no webhook resolves (neither per-suite nor workspace), the
        suite disabled alerting, or the run is below the suite's threshold. Raises on
        an HTTP error — the dispatch/composite layer isolates that so a flaky webhook
        can't fail the run or block the other channels.
        """
        config = notification_service.get_config(session, report.suite_id)
        if config is not None and not config.enabled:
            return
        policy = config.alert_on if config is not None else notification_service.DEFAULT_ALERT_ON
        route = route_for(report, policy)
        if not route.should_send:
            return
        webhook = notification_service.resolve_slack_webhook(
            config,
            secret_store=self._secret_store,
            workspace_secret_name=self._webhook_secret_name,
        )
        if not webhook:
            return
        # SSRF guard at the request sink: only POST to an https URL on an
        # allowlisted Slack host. The scheme check matters for the WORKSPACE webhook
        # too — it's never write-validated (only per-suite webhooks are), so an
        # http:// workspace URL would otherwise be POSTed in cleartext (#639 review).
        parsed = urlparse(webhook)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not any(
            host == a or host.endswith(f".{a}") for a in self._allowed_hosts
        ):
            log.warning("slack_webhook_not_allowed", run_id=str(report.run_id))
            return
        response = httpx.post(
            webhook, json=render_slack_message(report, route), timeout=self._timeout
        )
        response.raise_for_status()
        log.info(
            "slack_alert_sent",
            run_id=str(report.run_id),
            suite=report.suite_name,
            worst_severity=report.worst_severity,
            urgency=route.urgency,
            failed_checks=report.failed_checks,
        )
