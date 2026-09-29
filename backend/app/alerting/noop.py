"""The publisher used when no notification channel is configured."""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.app.alerting.base import ConnectionHealthReport, RunReport, WorkspaceSignalReport
from backend.app.core.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


class NoopPublisher:
    """Drops every report (logging at debug for traceability)."""

    def publish(self, session: Session, report: RunReport) -> None:
        log.debug(
            "result_publish_noop",
            run_id=str(report.run_id),
            suite=report.suite_name,
            run_status=report.run_status,
            worst_severity=report.worst_severity,
        )

    def publish_health(self, session: Session, report: ConnectionHealthReport) -> bool:
        log.debug(
            "health_publish_noop",
            connection_id=str(report.connection_id),
            state=report.state,
            consecutive_failures=report.consecutive_failures,
        )
        # The explicit test double COUNTS as delivered — it exists so tests can exercise the
        # stamped path; the real "nothing was sent" case is a real channel returning False (#1101,
        # mirroring publish_workspace_signal).
        return True

    def publish_workspace_signal(self, session: Session, report: WorkspaceSignalReport) -> bool:
        log.debug(
            "staleness_publish_noop",
            state=report.state,
            signal=type(report).__name__,
        )
        # The explicit test double COUNTS as delivered — it exists so tests can exercise the stamped
        # path; the real "nothing was sent" case is a real channel returning False.
        return True
