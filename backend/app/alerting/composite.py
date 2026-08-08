"""A ``ResultPublisher`` that fans a report out to several channels.

Each child publisher is best-effort and isolated: a raising / slow channel is
logged and the rest still run, so a broken Slack webhook can't suppress the
email (and vice versa). Children self-no-op when their channel is unconfigured,
so the composite can always hold all of them.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy.orm import Session

from backend.app.alerting.base import (
    AlertPublisher,
    AlertUndeliverableError,
    ConnectionHealthReport,
    PollStalenessReport,
    RunReport,
)
from backend.app.core.logging import get_logger

log = get_logger(__name__)


def _fan_out_delivered_first(
    publishers: Sequence[AlertPublisher],
    send: Callable[[AlertPublisher], bool],
    *,
    log_event: str,
    log_context: dict[str, Any],
    undeliverable_message: str,
) -> bool:
    """Shared delivered-first fan-out for the two health-family seam methods
    (:meth:`CompositePublisher.publish_health` / ``publish_poll_staleness``): dispatch
    ``send`` to every publisher, isolating a raising channel from the rest, and raise
    when **nothing** went out — every channel FAILED (re-raise the last error) or every
    channel quietly SKIPPED as unconfigured (raise :class:`AlertUndeliverableError`).
    Partial delivery (one channel down, another delivered) counts as delivered.

    Pulled out of the two near-identical loops (#1101 review finding): the two seam
    methods diverged once already — #1101 fixed ``publish_health`` to match a contract
    ``publish_poll_staleness`` had carried alone since #1100 — and a copy-pasted loop is
    exactly the shape that lets a third seam (or a future channel type) repeat the same
    omission.
    """
    delivered = 0
    last_error: Exception | None = None
    for publisher in publishers:
        try:
            if send(publisher):
                delivered += 1
        except Exception as exc:
            last_error = exc
            log.exception(log_event, channel=type(publisher).__name__, **log_context)
    if delivered == 0:
        if last_error is not None:
            raise last_error
        raise AlertUndeliverableError(undeliverable_message)
    return True


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

    def publish_health(self, session: Session, report: ConnectionHealthReport) -> bool:
        """Fan a connection poll-health edge out to every channel, isolating failures —
        the same contract as :meth:`publish` (#837), with the same delivered-first
        hinge as :meth:`publish_poll_staleness` below (#1101): the caller claims
        `health_alerted_at` BEFORE dispatching the send (#842/#843), so a quiet
        "every channel is unconfigured" must not read as delivered — that would
        permanently suppress the edge on a fresh install with zero channels
        configured, since the flag would already be set by the time an operator
        wires one up. Raises when nothing went out: every channel FAILED (re-raise
        the last error) or every channel SKIPPED (raise
        :class:`AlertUndeliverableError`). Partial delivery counts as delivered,
        exactly like the run path's channel isolation."""
        return _fan_out_delivered_first(
            self._publishers,
            lambda publisher: publisher.publish_health(session, report),
            log_event="channel_health_publish_failed",
            log_context={"connection_id": str(report.connection_id)},
            undeliverable_message=(
                "no alert channel is configured — the health edge was not delivered"
            ),
        )

    def publish_poll_staleness(self, session: Session, report: PollStalenessReport) -> bool:
        """Fan the workspace poll-staleness edge (#1052) out to every channel with the
        same isolation contract as the other two seam methods — with one difference
        that the caller relies on: **at least one channel must actually send** for
        the edge to count as delivered (#843's delivered-first rule needs a truthful
        answer), so this raises when nothing went out. Two ways to send nothing,
        both fatal here (review finding): every channel FAILED (re-raise the last
        error), or every channel quietly SKIPPED because none is configured (raise
        :class:`AlertUndeliverableError` — on a fresh install with blank channels a
        quiet return would stamp the flag with zero notifications sent, and the one
        alert whose job is to fire when everything else is silent would lie).
        Partial delivery counts as delivered, exactly like the per-connection edge's
        single-channel semantics."""
        return _fan_out_delivered_first(
            self._publishers,
            lambda publisher: publisher.publish_poll_staleness(session, report),
            log_event="channel_staleness_publish_failed",
            log_context={"state": report.state},
            undeliverable_message=(
                "no alert channel is configured — the poll-staleness edge was not delivered"
            ),
        )
