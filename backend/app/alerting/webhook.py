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
import re
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


_MISSING = object()
_PLACEHOLDER = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")


def _resolve_path(payload: dict[str, Any], path: str) -> Any:
    """Resolve a dot-path (list indices are numeric segments) against the
    already-redacted generic payload — a pure key/index lookup, nothing else,
    so a path can only ever reach a field the payload already exposes.
    """
    current: Any = payload
    for segment in path.split("."):
        if isinstance(current, dict):
            if segment not in current:
                return _MISSING
            current = current[segment]
        elif isinstance(current, list):
            if not segment.isdigit() or not (0 <= int(segment) < len(current)):
                return _MISSING
            current = current[int(segment)]
        else:
            return _MISSING
    return current


def _render_template_value(node: Any, payload: dict[str, Any]) -> Any:
    if isinstance(node, str):
        stripped = node.strip()
        exact = _PLACEHOLDER.fullmatch(stripped)
        if exact:
            # The whole string IS one placeholder — substitute the raw
            # resolved value (preserving its type: a number/bool/object/None,
            # not a stringified copy), so a template can produce e.g. a JSON
            # number or nested object field, not just text.
            resolved = _resolve_path(payload, exact.group(1))
            return None if resolved is _MISSING else resolved

        def _interpolate(match: re.Match[str]) -> str:
            resolved = _resolve_path(payload, match.group(1))
            return "" if resolved is _MISSING else str(resolved)

        return _PLACEHOLDER.sub(_interpolate, node)
    if isinstance(node, dict):
        return {key: _render_template_value(value, payload) for key, value in node.items()}
    if isinstance(node, list):
        return [_render_template_value(item, payload) for item in node]
    return node  # int/float/bool/None literals pass through unchanged


def render_templated_payload(payload: dict[str, Any], template: dict[str, Any]) -> dict[str, Any]:
    """Render a channel's custom payload template (#1663) against the already-
    redacted generic-webhook payload. A placeholder (``{{field.path}}``)
    resolves by KEY LOOKUP ONLY — there is no expression language, no code
    execution — so a template can rename/reshape/select the fields
    ``render_webhook_payload`` already exposes, and never reach anything it
    doesn't (the #1118/#1401 "no new exfiltration primitive" class). An
    unresolvable path degrades to ``null``/empty-string rather than raising —
    a template referencing a field this particular run doesn't have (e.g. no
    incidents) must not break delivery.
    """
    return {key: _render_template_value(value, payload) for key, value in template.items()}


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
        base_payload = render_webhook_payload(report)
        delivered = 0
        # destinations is already deduped by URL (channel_service.resolve_webhook_channels).
        for dest in destinations:
            # Re-checked at send time (not just at channel-config time): a
            # DNS-rebinding-style SSRF defends against the resolved address
            # changing after validation, same posture as Teams/Slack's
            # send-time host re-check.
            if not notification_service.is_safe_generic_webhook_url(dest.url):
                log.warning("webhook_destination_not_allowed", run_id=str(report.run_id))
                continue
            payload = (
                render_templated_payload(base_payload, dest.payload_template)
                if dest.payload_template is not None
                else base_payload
            )
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            headers = {
                "Content-Type": "application/json",
                _SIGNATURE_HEADER: _sign(body, dest.hmac_secret),
            }
            if dest.auth_header_name and dest.auth_header_value:
                headers[dest.auth_header_name] = dest.auth_header_value
            try:
                response = httpx.post(
                    dest.url, content=body, headers=headers, timeout=self._timeout
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
