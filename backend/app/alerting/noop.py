"""The publisher used when no notification channel is configured.

Until the Teams publisher is wired (and a webhook configured), the seam still
runs end to end — a report is built and dispatched — it just goes nowhere. This
keeps the run-completion hook always-present and exercised, so enabling a real
channel is purely additive.
"""

from __future__ import annotations

from backend.app.alerting.base import RunReport
from backend.app.core.logging import get_logger

log = get_logger(__name__)


class NoopPublisher:
    """Drops every report (logging at debug for traceability)."""

    def publish(self, report: RunReport) -> None:
        log.debug(
            "result_publish_noop",
            run_id=str(report.run_id),
            suite=report.suite_name,
            run_status=report.run_status,
            worst_severity=report.worst_severity,
        )
