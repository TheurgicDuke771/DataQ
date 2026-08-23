"""The run-completion hook: build a run's report and hand it to the publisher."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from backend.app.alerting import dedup, registry, suppression
from backend.app.alerting.base import AlertUndeliverableError, HealthState
from backend.app.alerting.builder import build_connection_health_report, build_run_report
from backend.app.alerting.routing import ALWAYS
from backend.app.core.logging import get_logger
from backend.app.db.models import Connection, Run
from backend.app.services import notification_service

log = get_logger(__name__)

# Terminal statuses worth notifying on. `cancelled` is excluded (user-initiated);
# `queued`/`running` are non-terminal so they never reach here.
_PUBLISHABLE_STATUSES = frozenset({"succeeded", "failed"})


def publish_run_outcome(session: Session, *, run_id: uuid.UUID) -> bool:
    """Publish ``run_id``'s outcome through the configured publisher."""
    try:
        run = session.get(Run, run_id)
        if run is None or run.status not in _PUBLISHABLE_STATUSES:
            return False
        # Suppress when every failing check is snoozed (the operator silenced them); a partial
        # snooze still alerts on the live checks.
        if suppression.all_failures_snoozed(session, run):
            log.info("alert_suppressed_snoozed", run_id=str(run_id), suite_id=str(run.suite_id))
            return False
        # Dedup before building/publishing: an ongoing, unchanged failure on a scheduled suite
        # shouldn't re-alert every run (a clean run is never a "duplicate".
        config = notification_service.get_config(session, run.suite_id)
        policy = config.alert_on if (config is not None and config.enabled) else None
        if policy != ALWAYS and dedup.is_duplicate_alert(session, run):
            log.info("alert_deduped", run_id=str(run_id), suite_id=str(run.suite_id))
            return False
        report = build_run_report(session, run)
        registry.get_result_publisher().publish(session, report)
        return True
    except Exception:
        log.exception("result_publish_failed", run_id=str(run_id))
        return False


def publish_connection_health(
    session: Session, *, connection_id: uuid.UUID, state: HealthState
) -> bool:
    """Publish a connection's poll-health **edge** (#837) — it started failing, or it
    recovered.
    """
    try:
        connection = session.get(Connection, connection_id)
        if connection is None:  # deleted between the poll and the alert
            return False
        report = build_connection_health_report(connection, state=state)
        # Propagate the publisher's own delivered/not-delivered answer rather than assuming True on
        # a normal return (review finding.
        return registry.get_health_publisher().publish_health(session, report)
    except AlertUndeliverableError:
        # Not a channel malfunction — every channel is simply unconfigured (or every configured one
        # failed).
        log.warning("connection_health_publish_undeliverable", connection_id=str(connection_id))
        return False
    except Exception:
        # The composite already logged this exact traceback once per failing channel, including this
        # one (the last), when every channel failed (#1226).
        log.exception("connection_health_publish_failed", connection_id=str(connection_id))
        return False
