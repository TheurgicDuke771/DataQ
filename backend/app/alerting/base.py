"""The ``ResultPublisher`` seam + the boundary-crossing report DTOs (ADR 0011)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol, runtime_checkable

# Failing severity tiers (worst last) — a run is alert-worthy when any check lands in one of these
# (or the run failed to execute).
from backend.app.db.models import FAILING_TIERS

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

__all__ = [
    "FAILING_TIERS",
    "HEALTH_FAILING",
    "HEALTH_RECOVERED",
    "AlertPublisher",
    "AlertUndeliverableError",
    "CheckReport",
    "ConnectionHealthReport",
    "HealthPublisher",
    "IncidentCard",
    "PollStalenessReport",
    "ResultPublisher",
    "RunReport",
    "mark_already_logged",
    "was_already_logged",
]

# The two connection-health transitions worth telling someone about (#837).
HealthState = Literal["failing", "recovered"]


class AlertUndeliverableError(RuntimeError):
    """Raised by the composite when a workspace-level edge reached NO channel —
    every channel either failed or quietly skipped as unconfigured. Callers doing
    #843 delivered-first bookkeeping catch this to leave the flag unset (retry next
    tick) instead of recording a delivery that never happened.
    """


# #1226: when EVERY channel fails, the composite's fan-out logs each failing channel with a full
# traceback (`log.exception`) before re-raising the LAST one so the caller can tell "genuinely
_ALREADY_LOGGED_ATTR = "_dataq_alerting_already_logged"


def mark_already_logged(exc: BaseException) -> None:
    """Tag ``exc`` as already logged with a full traceback by the composite
    fan-out, so the logging processor chain can downgrade a later
    ``log.exception(...)`` on it to a ``warning`` instead of re-logging the same
    traceback (#1226, centralized in the chain by #1261).
    """
    setattr(exc, _ALREADY_LOGGED_ATTR, True)


def was_already_logged(exc: BaseException) -> bool:
    """Whether ``exc`` was already logged with a full traceback by the composite fan-out (see
    :func:`mark_already_logged`). Read by the structlog processor chain
    (`core.logging._downgrade_already_logged_exceptions`, #1261), not by callers directly —
    nothing in ``backend/app`` should need to import this beyond the composite (which sets it).
    """
    return bool(getattr(exc, _ALREADY_LOGGED_ATTR, False))


HEALTH_FAILING: Final[HealthState] = "failing"
HEALTH_RECOVERED: Final[HealthState] = "recovered"


@dataclass(frozen=True)
class IncidentCard:
    """The stateful-incident reference a publisher carries alongside the per-result
    report (ADR 0034 #761). The alert stays per-result (its own dedup/snooze); this
    is the durable object it *references* so a ticket/webhook links to the open
    incident and arrives with the deterministic evidence card attached.
    """

    incident_id: uuid.UUID
    check_id: uuid.UUID
    check_name: str
    status: str
    occurrence_count: int
    is_new: bool
    evidence: dict[str, Any] | None


@dataclass(frozen=True)
class CheckReport:
    """One check's outcome, shaped for an outbound notification."""

    check_name: str
    expectation_type: str
    status: str
    metric_value: float | None
    observed_value: dict[str, Any] | None
    expected_value: dict[str, Any] | None
    sample_summary: dict[str, Any] | None


@dataclass(frozen=True)
class RunReport:
    """A completed run's redacted outcome — the unit a ``ResultPublisher`` sends."""

    run_id: uuid.UUID
    suite_id: uuid.UUID
    suite_name: str
    run_status: str
    datasource_type: str
    target_label: str
    worst_severity: str | None
    counts: dict[str, int]
    checks: list[CheckReport]
    finished_at: datetime | None
    # Run metadata for actionable alerts (#416) — env, when it ran, what triggered it, and a deep
    # link to the run-detail page.
    env: str | None = None
    started_at: datetime | None = None
    triggered_by: str | None = None
    run_url: str | None = None
    owner: str | None = None
    # The stateful incidents this run's failing checks reference (ADR 0034 #761) — one per breaching
    # check that has an active incident, each carrying its deterministic evidence card.
    incidents: list[IncidentCard] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float | None:
        """Wall-clock run duration in seconds, or ``None`` if either endpoint is
        missing (e.g. a run that failed before it started).
        """
        if self.started_at is None or self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def success(self) -> bool:
        """Data-quality verdict: the run executed cleanly *and* nothing breached.
        Derived (not stored) so it can never drift from ``worst_severity`` — the
        same drift-free pattern as the count properties below.
        """
        return self.run_status == "succeeded" and self.worst_severity is None

    @property
    def total_checks(self) -> int:
        return sum(self.counts.values())

    @property
    def failed_checks(self) -> int:
        """Checks that genuinely breached (``fail`` + ``critical``) — ``warn`` is
        surfaced via ``worst_severity``, not counted as a failure here.
        """
        return self.counts.get("fail", 0) + self.counts.get("critical", 0)

    @property
    def has_failures(self) -> bool:
        """Alert-worthy: the run couldn't execute, or any check breached a tier
        (incl. ``warn``). Publishers/routing refine *whether* to send on top of
        this (severity-aware routing + the per-suite on-fail/warn/always policy).
        """
        return self.run_status == "failed" or any(
            self.counts.get(tier, 0) for tier in FAILING_TIERS
        )


@dataclass(frozen=True)
class ConnectionHealthReport:
    """A connection's poll-health transition — the unit a ``HealthPublisher`` sends."""

    connection_id: uuid.UUID
    connection_name: str
    connection_type: str
    state: HealthState
    consecutive_failures: int
    reason: str | None
    last_polled_at: datetime | None
    connection_url: str | None = None

    @property
    def is_failing(self) -> bool:
        """Whether this is the failure edge (vs the recovery edge)."""
        return self.state == HEALTH_FAILING


@dataclass(frozen=True)
class PollStalenessReport:
    """Workspace-wide orchestration-poll staleness (#1052) — the signal that cannot lie."""

    state: HealthState
    connection_count: int
    most_recent_polled_at: datetime | None
    threshold_seconds: int

    @property
    def is_failing(self) -> bool:
        """Whether this is the failure edge (vs the recovery edge)."""
        return self.state == HEALTH_FAILING


@runtime_checkable
class ResultPublisher(Protocol):
    """Sends a completed run's redacted ``RunReport`` to an external channel."""

    def publish(self, session: Session, report: RunReport) -> None: ...


@runtime_checkable
class HealthPublisher(Protocol):
    """Sends a connection's poll-health transition to an external channel."""

    def publish_health(self, session: Session, report: ConnectionHealthReport) -> bool:
        """Deliver a connection's poll-health edge (#837); return whether a message
        actually left this process (``False`` = quietly skipped, e.g. the channel is
        unconfigured — a skip must never read as delivered, #1101).
        """
        ...

    def publish_poll_staleness(self, session: Session, report: PollStalenessReport) -> bool:
        """Deliver the workspace-wide poll-staleness edge (#1052); return whether a
        message actually left this process (``False`` = quietly skipped, e.g. the
        channel is unconfigured — a skip must never read as delivered).
        """
        ...


@runtime_checkable
class AlertPublisher(ResultPublisher, HealthPublisher, Protocol):
    """A channel that can deliver **both** a run outcome and a connection-health edge."""
