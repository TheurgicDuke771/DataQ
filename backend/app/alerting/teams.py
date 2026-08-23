"""The v1 ``ResultPublisher`` — posts a run's report as a Teams Adaptive Card."""

from __future__ import annotations

from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from backend.app.alerting.base import ConnectionHealthReport, PollStalenessReport, RunReport
from backend.app.alerting.card import (
    render_teams_health_message,
    render_teams_message,
    render_teams_staleness_message,
)
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
        """Deliver the run's card per its suite's notification config."""
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

    def publish_health(self, session: Session, report: ConnectionHealthReport) -> bool:
        """Post a connection poll-health edge to the **workspace** webhook (#837)."""
        webhook = notification_service.resolve_webhook(
            None,
            secret_store=self._secret_store,
            workspace_secret_name=self._workspace_secret_name,
        )
        if not webhook:
            return False
        if not _webhook_allowed(webhook):
            log.warning("teams_webhook_host_not_allowed", connection_id=str(report.connection_id))
            return False
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
        return True

    def publish_poll_staleness(self, session: Session, report: PollStalenessReport) -> bool:
        """Post the workspace poll-staleness edge (#1052) to the workspace webhook — same
        resolution as :meth:`publish_health`, but returning **whether a message was actually
        posted**: an unconfigured/ineligible webhook is ``False``, never a quiet success (review
        finding — the delivered-first flag must not be stamped by a channel that sent nothing).
        """
        webhook = notification_service.resolve_webhook(
            None,
            secret_store=self._secret_store,
            workspace_secret_name=self._workspace_secret_name,
        )
        if not webhook:
            return False
        if not _webhook_allowed(webhook):
            log.warning("teams_webhook_host_not_allowed", signal="poll_staleness")
            return False
        response = httpx.post(
            webhook, json=render_teams_staleness_message(report), timeout=self._timeout
        )
        response.raise_for_status()
        log.info(
            "teams_staleness_alert_sent",
            state=report.state,
            connection_count=report.connection_count,
        )
        return True


def _webhook_allowed(webhook: str) -> bool:
    """SSRF guard at the request sink: the webhook is user-supplied, so only post to an
    **https** URL on an allowlisted host. upsert validates on write; this re-checks at
    send time (rotated / workspace secrets — defence in depth), and is shared by the run +
    health paths so the two can't drift apart on which hosts are reachable.
    """
    parsed = urlparse(webhook)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(
        host == allowed or host.endswith(f".{allowed}")
        for allowed in notification_service.allowed_webhook_hosts()
    )
