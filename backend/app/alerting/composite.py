"""A ``ResultPublisher`` that fans a report out to several channels.

Each child publisher is best-effort and isolated: a raising / slow channel is
logged and the rest still run, so a broken Slack webhook can't suppress the
email (and vice versa). Children self-no-op when their channel is unconfigured,
so the composite can always hold all of them.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.orm import Session

from backend.app.alerting.base import (
    AlertPublisher,
    ConnectionHealthReport,
    PollStalenessReport,
    RunReport,
)
from backend.app.core.logging import get_logger

log = get_logger(__name__)


class CompositePublisher:
    """Delivers a report through every child publisher, isolating failures."""

    def __init__(self, publishers: Sequence[AlertPublisher]) -> None:
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

    def publish_health(self, session: Session, report: ConnectionHealthReport) -> None:
        """Fan a connection poll-health edge out to every channel, isolating failures —
        the same contract as :meth:`publish` (#837). A broken Slack webhook must not
        swallow the email telling you your poll has been dead for half an hour."""
        for publisher in self._publishers:
            try:
                publisher.publish_health(session, report)
            except Exception:
                log.exception(
                    "channel_health_publish_failed",
                    channel=type(publisher).__name__,
                    connection_id=str(report.connection_id),
                )

    def publish_poll_staleness(self, session: Session, report: PollStalenessReport) -> None:
        """Fan the workspace poll-staleness edge (#1052) out to every channel with the
        same isolation contract as the other two seam methods — with one difference
        that the caller relies on: **at least one channel must succeed** for the edge
        to count as delivered (#843's delivered-first rule needs a truthful answer),
        so this re-raises when every channel failed. Partial delivery counts as
        delivered, exactly like the per-connection edge's single-channel semantics."""
        delivered = 0
        last_error: Exception | None = None
        for publisher in self._publishers:
            try:
                publisher.publish_poll_staleness(session, report)
                delivered += 1
            except Exception as exc:
                last_error = exc
                log.exception(
                    "channel_staleness_publish_failed",
                    channel=type(publisher).__name__,
                    state=report.state,
                )
        if delivered == 0 and last_error is not None:
            raise last_error
