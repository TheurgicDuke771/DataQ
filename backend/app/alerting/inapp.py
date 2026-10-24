"""The in-app ``ResultPublisher`` (#1687): a run outcome lands in the feed of every user who
can see the suite. Runs after the same per-suite policy + routing gate as the outbound
channels; dedup and snooze were already applied before any publisher is reached
(``dispatch.publish_run_outcome``), so the feed never says what Teams/Slack/email did not.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.alerting.base import ConnectionHealthReport, RunReport, WorkspaceSignalReport
from backend.app.alerting.routing import route_for
from backend.app.core import usage_telemetry
from backend.app.core.logging import get_logger
from backend.app.services import notification_feed_service, notification_service

log = get_logger(__name__)


class InAppPublisher:
    """Writes a run's outcome into the recipients' feeds in the run's own session."""

    def publish(self, session: Session, report: RunReport) -> None:
        config = notification_service.get_config(session, report.suite_id)
        if config is not None and not config.enabled:
            return
        policy = config.alert_on if config is not None else notification_service.DEFAULT_ALERT_ON
        route = route_for(report, policy)
        if not route.should_send:
            return
        try:
            # A savepoint: a failed insert must not leave the worker's session in an aborted
            # transaction for the channels and hooks that run after this one.
            with session.begin_nested():
                inserted = notification_feed_service.record_run_alert(session, report)
            session.commit()
        except Exception:
            # Named here (the composite logs the generic `channel_publish_failed` with the
            # traceback) so a feed-only outage is searchable on its own; then re-raised so
            # the composite's isolation and logging apply unchanged.
            log.warning("inapp_feed_write_failed", run_id=str(report.run_id))
            raise
        if inserted == 0:
            # Re-publish of an outcome already in every feed (worker retry), or a suite nobody
            # can see — neither is a delivery, so no telemetry event.
            log.info("inapp_alert_skipped", run_id=str(report.run_id), suite=report.suite_name)
            return
        usage_telemetry.emit(
            usage_telemetry.UsageEvent.ALERT_DELIVERED, session=session, channel="in_app"
        )
        log.info(
            "inapp_alert_recorded",
            run_id=str(report.run_id),
            suite=report.suite_name,
            recipients=inserted,
            worst_severity=report.worst_severity,
            urgency=route.urgency,
        )

    def publish_health(self, session: Session, report: ConnectionHealthReport) -> bool:
        """Not delivered here: a connection-health edge has no suite, and the feed is scoped
        by suite grants. ``False`` so the caller's delivered-first flag is never stamped by a
        channel that wrote nothing (#1101).
        """
        return False

    def publish_workspace_signal(self, session: Session, report: WorkspaceSignalReport) -> bool:
        """Not delivered here — same reason as :meth:`publish_health`."""
        return False
