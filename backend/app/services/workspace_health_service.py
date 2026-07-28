"""Workspace-wide orchestration-poll staleness — the signal that cannot lie (#1052).

Every incident in the #905 class (#852 exporter starvation, #854 unbounded row-lock
wait, the 2026-07-18 wedged broker reconnect) had the same shape: **the worker looked
alive and wrote nothing**. A per-connection health edge (#837/#996) is computed from
state the worker itself writes, so it structurally cannot fire when the worker is the
thing that died. The DB is the only party that can tell: if ``max(last_polled_at)``
across ALL orchestration connections is older than a few poll intervals, the polling
loop is dead regardless of cause.

This module therefore runs from the **API process** (a lifespan loop in ``main.py``),
never the worker — a check that lives in the process it monitors inherits the failure
it exists to detect. Delivery reuses the ``HealthPublisher`` seam and the #843
delivered-first rule via a ``workspace_health`` row (no parallel mechanism): the
FAILING edge is recorded only after a publish actually succeeded, the RECOVERED edge
only fires when a FAILING one was delivered, and the row is claimed
``FOR UPDATE SKIP LOCKED`` so two API replicas never double-send.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.app.alerting.base import HEALTH_FAILING, HEALTH_RECOVERED, PollStalenessReport
from backend.app.alerting.registry import get_health_publisher
from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger
from backend.app.db.models import ORCHESTRATION_PROVIDERS, Connection, WorkspaceHealth

log = get_logger(__name__)

#: The `workspace_health.key` this signal owns.
POLL_STALENESS_KEY = "orchestration_poll_staleness"


def evaluate_poll_staleness(
    session: Session, *, now: datetime | None = None
) -> tuple[bool, PollStalenessReport]:
    """Pure decision: is the workspace's polling loop stale, and the report to say so.

    Returns ``(stale, report)`` — the report carries the FAILING state; the caller
    flips it to RECOVERED for the recovery edge. Reference moment per connection is
    ``last_polled_at``; a connection that has **never** been polled falls back to its
    ``created_at``, so a poller that never ran at all (wrong image, task never
    registered) still goes stale once the oldest connection has waited out the
    threshold — "we have not looked yet" must not read as "nothing to report" (#828).

    No orchestration connections ⇒ not stale (nothing to poll is not a dead loop).
    """
    settings = get_settings()
    threshold = settings.poll_staleness_alert_after_s
    moment = now or datetime.now(UTC)
    count, most_recent_poll, reference = session.execute(
        select(
            func.count(),
            func.max(Connection.last_polled_at),
            func.max(func.coalesce(Connection.last_polled_at, Connection.created_at)),
        ).where(Connection.type.in_(ORCHESTRATION_PROVIDERS))
    ).one()
    stale = bool(
        threshold > 0
        and count
        and reference is not None
        and reference < moment - timedelta(seconds=threshold)
    )
    report = PollStalenessReport(
        state=HEALTH_FAILING,
        connection_count=int(count or 0),
        most_recent_polled_at=most_recent_poll,
        threshold_seconds=threshold,
    )
    return stale, report


def run_poll_staleness_check(session: Session, *, now: datetime | None = None) -> str:
    """One tick of the API-side staleness check; returns the outcome for logs/tests.

    Outcomes: ``disabled`` · ``skipped`` (another replica holds the claim) ·
    ``ok`` (nothing to say) · ``alerted`` · ``recovered``.

    #843 delivered-first, both edges: ``alerted_at`` is written only **after**
    ``publish_poll_staleness`` returned (the composite raises when every channel
    failed, so a total delivery failure leaves the flag unset and the next tick
    retries); the RECOVERED edge fires only when a FAILING edge was actually
    delivered, and clears the flag the same way.
    """
    if get_settings().poll_staleness_alert_after_s <= 0:
        return "disabled"

    # Claim the signal row (creating it on first use). SKIP LOCKED: with N API
    # replicas each running this loop, one claims, the rest skip the tick — the
    # cadence is minutes, so a skipped tick costs nothing and can never double-send.
    _ensure_row(session)
    flag = session.execute(
        select(WorkspaceHealth)
        .where(WorkspaceHealth.key == POLL_STALENESS_KEY)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if flag is None:
        session.rollback()
        return "skipped"

    stale, report = evaluate_poll_staleness(session, now=now)
    outstanding = flag.alerted_at is not None

    if stale and not outstanding:
        get_health_publisher().publish_poll_staleness(session, report)
        flag.alerted_at = now or datetime.now(UTC)
        session.commit()
        log.warning(
            "workspace_poll_staleness_alerted",
            connection_count=report.connection_count,
            most_recent_polled_at=(
                report.most_recent_polled_at.isoformat() if report.most_recent_polled_at else None
            ),
            threshold_s=report.threshold_seconds,
        )
        return "alerted"

    if not stale and outstanding:
        recovery = PollStalenessReport(
            state=HEALTH_RECOVERED,
            connection_count=report.connection_count,
            most_recent_polled_at=report.most_recent_polled_at,
            threshold_seconds=report.threshold_seconds,
        )
        get_health_publisher().publish_poll_staleness(session, recovery)
        flag.alerted_at = None
        session.commit()
        log.info("workspace_poll_staleness_recovered", connection_count=report.connection_count)
        return "recovered"

    session.rollback()  # release the row lock; nothing to record
    return "ok"


def _ensure_row(session: Session) -> None:
    """Create the signal row if absent (idempotent, race-safe via ON CONFLICT)."""
    session.execute(
        pg_insert(WorkspaceHealth)
        .values(key=POLL_STALENESS_KEY)
        .on_conflict_do_nothing(index_elements=[WorkspaceHealth.key])
    )
