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
        """Send the run's card, if it has failures and a webhook is configured.

        Raises on an HTTP error — the dispatch layer isolates that so a flaky
        webhook can't fail the run. A clean run (nothing breached) or an
        unresolved webhook is a silent no-op, not a send.
        """
        if not report.has_failures:
            return
        webhook = self._resolve_webhook(report)
        if not webhook:
            return
        message = render_teams_message(report)
        response = httpx.post(webhook, json=message, timeout=self._timeout)
        response.raise_for_status()
        log.info(
            "teams_alert_sent",
            run_id=str(report.run_id),
            suite=report.suite_name,
            worst_severity=report.worst_severity,
            failed_checks=report.failed_checks,
        )
