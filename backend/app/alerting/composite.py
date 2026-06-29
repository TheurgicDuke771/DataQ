"""A ``ResultPublisher`` that fans a report out to several channels.

Each child publisher is best-effort and isolated: a raising / slow channel is
logged and the rest still run, so a broken Slack webhook can't suppress the
email (and vice versa). Children self-no-op when their channel is unconfigured,
so the composite can always hold all of them.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.orm import Session

from backend.app.alerting.base import ResultPublisher, RunReport
from backend.app.core.logging import get_logger

log = get_logger(__name__)


class CompositePublisher:
    """Delivers a report through every child publisher, isolating failures."""

    def __init__(self, publishers: Sequence[ResultPublisher]) -> None:
        self._publishers = tuple(publishers)

    def publish(self, session: Session, report: RunReport) -> None:
        for publisher in self._publishers:
            try:
                publisher.publish(session, report)
            except Exception:
                # One channel failing must not stop the others or fail the run.
                log.exception(
                    "channel_publish_failed",
                    channel=type(publisher).__name__,
                    run_id=str(report.run_id),
                )
