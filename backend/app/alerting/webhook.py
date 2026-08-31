"""A generic HMAC-signed outbound webhook ``ResultPublisher`` (#1662) — the
vendor-neutral way to reach an enterprise receiver (PagerDuty, Opsgenie,
ServiceNow, Jira, or a self-hosted endpoint) with no per-vendor code. Mirrors
the Airflow *ingest* HMAC scheme (``api/v1/orchestration.py``) in reverse:
DataQ signs the outbound body the same way it verifies an inbound one.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import httpx
from sqlalchemy.orm import Session

from backend.app.alerting.base import (
    CheckReport,
    ConnectionHealthReport,
    IncidentCard,
    PollStalenessReport,
    RunReport,
)
from backend.app.alerting.routing import route_for
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.services import channel_service, notification_service

log = get_logger(__name__)

_POST_TIMEOUT_SECONDS = 10.0
_SIGNATURE_HEADER = "X-DataQ-Signature"


def _check_payload(check: CheckReport) -> dict[str, Any]:
    return {
        "check_name": check.check_name,
        "expectation_type": check.expectation_type,
        "status": check.status,
        "metric_value": check.metric_value,
        # Already redacted upstream (builder.py, via the same column-aware ladder
        # every other alert surface uses) — the webhook payload does no
        # redaction of its own, it just carries what RunReport already carries.
        "observed_value": check.observed_value,
        "expected_value": check.expected_value,
        "sample_summary": check.sample_summary,
    }


def _incident_payload(card: IncidentCard) -> dict[str, Any]:
    return {
        "incident_id": str(card.incident_id),
        "check_id": str(card.check_id),
        "check_name": card.check_name,
        "status": card.status,
        "occurrence_count": card.occurrence_count,
        "is_new": card.is_new,
        "evidence": card.evidence,
        "narrative": card.narrative,
    }


def render_webhook_payload(report: RunReport) -> dict[str, Any]:
    """The generic-webhook JSON payload — the same redacted data every other
    alert surface sends, shaped for a machine receiver rather than a chat
    card: flat fields, no vendor-specific formatting.
    """
    return {
        "event": "run.completed",
        "run_id": str(report.run_id),
        "suite_id": str(report.suite_id),
        "suite_name": report.suite_name,
        "run_status": report.run_status,
        "datasource_type": report.datasource_type,
        "target_label": report.target_label,
        "worst_severity": report.worst_severity,
        "success": report.success,
        "total_checks": report.total_checks,
        "failed_checks": report.failed_checks,
        "counts": report.counts,
        "checks": [_check_payload(c) for c in report.checks],
        "env": report.env,
        "started_at": report.started_at.isoformat() if report.started_at else None,
        "finished_at": report.finished_at.isoformat() if report.finished_at else None,
        "duration_seconds": report.duration_seconds,
        "triggered_by": report.triggered_by,
        "run_url": report.run_url,
        "owner": report.owner,
        "incidents": [_incident_payload(c) for c in report.incidents],
    }


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


class WebhookPublisher:
    """Posts a run's report as an HMAC-signed JSON body to every suite-linked
    ``webhook`` channel (#1514's channel model, #1662).

    Unlike Teams/Slack/email, a generic webhook has no workspace-level
    fallback — there is no single "the" destination for an arbitrary vendor
    receiver, only per-suite-linked channels. `publish_health` /
    `publish_poll_staleness` (workspace-wide signals) are therefore honest
    no-ops here: they quietly report nothing delivered rather than pretending
    a channel type with no workspace concept could ever carry them.
    """

    def __init__(
        self, *, secret_store: SecretStore, timeout: float = _POST_TIMEOUT_SECONDS
    ) -> None:
        self._secret_store = secret_store
        self._timeout = timeout

    def publish(self, session: Session, report: RunReport) -> None:
        config = notification_service.get_config(session, report.suite_id)
        if config is not None and not config.enabled:
            return
        policy = config.alert_on if config is not None else notification_service.DEFAULT_ALERT_ON
        route = route_for(report, policy)
        if not route.should_send:
            return
        destinations = channel_service.resolve_webhook_channels(
            session, report.suite_id, secret_store=self._secret_store
        )
        if not destinations:
            return
        payload = render_webhook_payload(report)
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        delivered = 0
        seen: set[str] = set()
        for url, hmac_secret in destinations:
            if url in seen:
                continue
            seen.add(url)
            # Re-checked at send time (not just at channel-config time): a
            # DNS-rebinding-style SSRF defends against the resolved address
            # changing after validation, same posture as Teams/Slack's
            # send-time host re-check.
            if not notification_service.is_safe_generic_webhook_url(url):
                log.warning("webhook_destination_not_allowed", run_id=str(report.run_id))
                continue
            signature = _sign(body, hmac_secret)
            try:
                response = httpx.post(
                    url,
                    content=body,
                    headers={"Content-Type": "application/json", _SIGNATURE_HEADER: signature},
                    timeout=self._timeout,
                )
                response.raise_for_status()
            except Exception:
                # One bad destination must not block delivery to the others.
                log.exception("webhook_destination_send_failed", run_id=str(report.run_id))
                continue
            delivered += 1
            log.info(
                "webhook_alert_sent",
                run_id=str(report.run_id),
                suite=report.suite_name,
                worst_severity=report.worst_severity,
                urgency=route.urgency,
                failed_checks=report.failed_checks,
            )
        if delivered == 0:
            # Every destination failed/was blocked — the aggregate signal
            # CompositePublisher's own try/except would otherwise see, restored
            # here since fan-out means one destination failing must never
            # propagate and abort the other channel types' delivery.
            log.warning(
                "channel_publish_failed", channel="WebhookPublisher", run_id=str(report.run_id)
            )

    def publish_health(self, session: Session, report: ConnectionHealthReport) -> bool:
        return False

    def publish_poll_staleness(self, session: Session, report: PollStalenessReport) -> bool:
        return False
