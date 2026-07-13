"""The v1 ``ResultPublisher`` — posts a run's report as a Teams Adaptive Card.

Delivery is driven by the run's **per-suite** notification config (read from the
dispatch session): whether the suite has alerting enabled, its threshold
(``alert_on`` → routing policy), and which webhook to post to (the per-suite one,
falling back to the workspace webhook). A suite with notifications disabled, a
run below its threshold, or no resolvable webhook is a quiet no-op — alerts are
for what the suite asked to be told about.
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from backend.app.alerting.base import ConnectionHealthReport, RunReport
from backend.app.alerting.card import render_teams_health_message, render_teams_message
from backend.app.alerting.routing import route_for
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.services import notification_service

log = get_logger(__name__)

_POST_TIMEOUT_SECONDS = 10.0


class TeamsPublisher:
    """Posts an Adaptive Card to the webhook resolved for the run's suite."""

    def __init__(
        self,
        *,
        secret_store: SecretStore,
        workspace_secret_name: str | None,
        timeout: float = _POST_TIMEOUT_SECONDS,
    ) -> None:
        self._secret_store = secret_store
        self._workspace_secret_name = workspace_secret_name
        self._timeout = timeout

    def publish(self, session: Session, report: RunReport) -> None:
        """Deliver the run's card per its suite's notification config.

        Skips silently when the suite disabled alerting, the run is below the
        suite's threshold, or no webhook resolves. Raises on an HTTP error — the
        dispatch layer isolates that so a flaky webhook can't fail the run.
        """
        config = notification_service.get_config(session, report.suite_id)
        if config is not None and not config.enabled:
            return
        policy = config.alert_on if config is not None else notification_service.DEFAULT_ALERT_ON
        route = route_for(report, policy)
        if not route.should_send:
            return
        webhook = notification_service.resolve_webhook(
            config,
            secret_store=self._secret_store,
            workspace_secret_name=self._workspace_secret_name,
        )
        if not webhook:
            return
        if not _webhook_allowed(webhook):
            log.warning("teams_webhook_host_not_allowed", run_id=str(report.run_id))
            return
        response = httpx.post(
            webhook, json=render_teams_message(report, route), timeout=self._timeout
        )
        response.raise_for_status()
        log.info(
            "teams_alert_sent",
            run_id=str(report.run_id),
            suite=report.suite_name,
            worst_severity=report.worst_severity,
            urgency=route.urgency,
            failed_checks=report.failed_checks,
        )

    def publish_health(self, session: Session, report: ConnectionHealthReport) -> None:
        """Post a connection poll-health edge to the **workspace** webhook (#837).

        No per-suite config applies (a connection has no suite), so this resolves the
        workspace webhook only — `resolve_webhook(None, …)` is exactly that fallback.
        Whether to alert was already decided at the threshold crossing; here we just
        deliver. Quiet no-op when no workspace webhook is configured.
        """
        webhook = notification_service.resolve_webhook(
            None,
            secret_store=self._secret_store,
            workspace_secret_name=self._workspace_secret_name,
        )
        if not webhook:
            return
        if not _webhook_allowed(webhook):
            log.warning("teams_webhook_host_not_allowed", connection_id=str(report.connection_id))
            return
        response = httpx.post(
            webhook, json=render_teams_health_message(report), timeout=self._timeout
        )
        response.raise_for_status()
        log.info(
            "teams_health_alert_sent",
            connection_id=str(report.connection_id),
            state=report.state,
            consecutive_failures=report.consecutive_failures,
        )


def _webhook_allowed(webhook: str) -> bool:
    """SSRF guard at the request sink: the webhook is user-supplied, so only post to an
    allowlisted host. upsert validates on write; this re-checks at send time (rotated /
    workspace secrets — defence in depth), and is shared by the run + health paths so
    the two can't drift apart on which hosts are reachable."""
    host = (urlparse(webhook).hostname or "").lower()
    return any(
        host == allowed or host.endswith(f".{allowed}")
        for allowed in notification_service.allowed_webhook_hosts()
    )
