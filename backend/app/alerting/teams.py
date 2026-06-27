"""The v1 ``ResultPublisher`` — posts a run's report as a Teams Adaptive Card.

The webhook URL is resolved **per report** by an injected resolver, not baked
into the publisher: v1 resolves a single workspace webhook, and per-suite
notification config (a later PR) extends the resolver without touching the
publisher or the seam. A run with nothing to report, or no webhook for its suite,
is a quiet no-op — alerts are for failures, not green runs.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx

from backend.app.alerting.base import RunReport
from backend.app.alerting.card import render_teams_message
from backend.app.alerting.routing import route_for
from backend.app.core.logging import get_logger

log = get_logger(__name__)

# Resolve the Teams webhook URL for a given run (None → no channel, skip).
WebhookResolver = Callable[[RunReport], str | None]

_POST_TIMEOUT_SECONDS = 10.0


class TeamsPublisher:
    """Posts an Adaptive Card to the webhook the resolver returns for the run."""

    def __init__(
        self, resolve_webhook: WebhookResolver, *, timeout: float = _POST_TIMEOUT_SECONDS
    ) -> None:
        self._resolve_webhook = resolve_webhook
        self._timeout = timeout

    def publish(self, report: RunReport) -> None:
        """Send the run's card, per the severity route, if a webhook is configured.

        The send decision + prominence come from ``routing.route_for`` (the one
        policy point): a clean run isn't sent, and the card's urgency/escalation
        follow the route. Raises on an HTTP error — the dispatch layer isolates
        that so a flaky webhook can't fail the run; an unresolved webhook is a
        silent no-op.
        """
        route = route_for(report)
        if not route.should_send:
            return
        webhook = self._resolve_webhook(report)
        if not webhook:
            return
        message = render_teams_message(report, route)
        response = httpx.post(webhook, json=message, timeout=self._timeout)
        response.raise_for_status()
        log.info(
            "teams_alert_sent",
            run_id=str(report.run_id),
            suite=report.suite_name,
            worst_severity=report.worst_severity,
            urgency=route.urgency,
            failed_checks=report.failed_checks,
        )
