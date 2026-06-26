"""The publisher used when no notification channel is configured.

Until a webhook is configured (per-suite or workspace) the seam still runs end to
end — a report is built and dispatched — it just goes nowhere. This keeps the
run-completion hook always-present and exercised.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.app.alerting.base import RunReport
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
