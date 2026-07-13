"""The ``ResultPublisher`` seam + the boundary-crossing report DTOs (ADR 0011).

A ``RunReport`` is the GX-agnostic, **already-redacted** summary of a completed
run that a publisher sends outside DataQ's trust boundary. It carries enough for
a Teams card / test-management push (suite, datasource, per-check status,
observed vs expected, *how many* rows failed) but never the raw failing rows —
``CheckReport.sample_summary`` is the redacted (counts-only) form produced at the
seam, so no publisher can leak PII even by accident.

Publishers depend only on these types, never on the ORM or GX internals — the
same discipline as the ``CheckRunner`` / ``OrchestrationProvider`` seams.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol, runtime_checkable

# Failing severity tiers (worst last) — a run is alert-worthy when any check lands
# in one of these (or the run failed to execute). `pass` is clean; `skip`/`error`
# are operational, not data-quality severities (ADR 0005). Single-sourced with the
# severity rank in db.models (#655); re-exported here for the alerting layer.
from backend.app.db.models import FAILING_TIERS

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

__all__ = [
    "FAILING_TIERS",
    "HEALTH_FAILING",
    "HEALTH_RECOVERED",
    "AlertPublisher",
    "CheckReport",
    "ConnectionHealthReport",
    "HealthPublisher",
    "IncidentCard",
    "ResultPublisher",
    "RunReport",
]

# The two connection-health transitions worth telling someone about (#837). Both are
# *edges*, not states: the poller emits one when a connection crosses the failure
# threshold and one when it comes back — never once per failing poll.
#
# The state is a Literal, not a free str: `is_failing` is `state == HEALTH_FAILING`, so
# ANY other value renders as a *recovery* — a typo'd call site (`"failed"`, `"FAILING"`)
# would send a confident all-clear for a connection that is dead, silently. A field whose
# two values mean opposite things must not admit a third, so mypy rejects it at the call
# site rather than the operator discovering it at 3am.
HealthState = Literal["failing", "recovered"]

HEALTH_FAILING: Final[HealthState] = "failing"
HEALTH_RECOVERED: Final[HealthState] = "recovered"


@dataclass(frozen=True)
class IncidentCard:
    """The stateful-incident reference a publisher carries alongside the per-result
    report (ADR 0034 #761). The alert stays per-result (its own dedup/snooze); this
    is the durable object it *references* so a ticket/webhook links to the open
    incident and arrives with the deterministic evidence card attached.

    ``is_new`` distinguishes a freshly-opened incident from an occurrence attached
    to an already-open one (``occurrence_count`` > 1). ``evidence`` is the
    already-redacted layer-1 card (no ``sample_failures`` content) as snapshotted
    on the incident — passed through opaque; a publisher renders what it needs.
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
    """One check's outcome, shaped for an outbound notification.

    ``observed_value`` / ``expected_value`` are GX aggregates as stored (already
    JSON-sanitized at write time). ``sample_summary`` is the **redacted** form of
    the result's ``sample_failures`` — counts/percentages only, raw cell values
    masked — so a card can say "12 rows failed" without leaking which.
    """

    check_name: str
    expectation_type: str
    status: str
    metric_value: float | None
    observed_value: dict[str, Any] | None
    expected_value: dict[str, Any] | None
    sample_summary: dict[str, Any] | None


@dataclass(frozen=True)
class RunReport:
    """A completed run's redacted outcome — the unit a ``ResultPublisher`` sends.

    ``run_status`` is the run *lifecycle* (``succeeded``/``failed``); ``success``
    is the derived data-quality verdict (every check passed). ``counts`` is the
    per-status histogram the derived count properties read, and ``worst_severity``
    is the highest failing tier present (``None`` when nothing breached).
    """

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
    # Run metadata for actionable alerts (#416) — env, when it ran, what triggered
    # it, and a deep link to the run-detail page. All optional/defaulted so existing
    # constructors keep working; `run_url` is None when no public base URL is set.
    env: str | None = None
    started_at: datetime | None = None
    triggered_by: str | None = None
    run_url: str | None = None
    owner: str | None = None
    # The stateful incidents this run's failing checks reference (ADR 0034 #761) —
    # one per breaching check that has an active incident, each carrying its
    # deterministic evidence card. Empty when the run is clean or its asset never
    # resolved (no anchor). Defaulted so existing constructors keep working.
    incidents: list[IncidentCard] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float | None:
        """Wall-clock run duration in seconds, or ``None`` if either endpoint is
        missing (e.g. a run that failed before it started)."""
        if self.started_at is None or self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def success(self) -> bool:
        """Data-quality verdict: the run executed cleanly *and* nothing breached.
        Derived (not stored) so it can never drift from ``worst_severity`` — the
        same drift-free pattern as the count properties below."""
        return self.run_status == "succeeded" and self.worst_severity is None

    @property
    def total_checks(self) -> int:
        return sum(self.counts.values())

    @property
    def failed_checks(self) -> int:
        """Checks that genuinely breached (``fail`` + ``critical``) — ``warn`` is
        surfaced via ``worst_severity``, not counted as a failure here."""
        return self.counts.get("fail", 0) + self.counts.get("critical", 0)

    @property
    def has_failures(self) -> bool:
        """Alert-worthy: the run couldn't execute, or any check breached a tier
        (incl. ``warn``). Publishers/routing refine *whether* to send on top of
        this (severity-aware routing + the per-suite on-fail/warn/always policy)."""
        return self.run_status == "failed" or any(
            self.counts.get(tier, 0) for tier in FAILING_TIERS
        )


@dataclass(frozen=True)
class ConnectionHealthReport:
    """A connection's poll-health transition — the unit a ``HealthPublisher`` sends.

    Deliberately **not** a ``RunReport`` (#837): a connection whose poll is failing has
    no suite, no checks and no severity, and shoehorning one into the run shape would
    put a fake run in every channel's card. It is its own DTO behind its own seam.

    ``reason`` is the **classified** failure (``Connection.last_poll_error``, produced by
    `classify_failure_reason`) — never raw exception text. That is load-bearing rather
    than tidy: the outage this feature exists for (#828) raised an auth error whose
    message carried the SAS query string, and an alert is the one place that string
    would leave DataQ's trust boundary. ``None`` on recovery (nothing is wrong).
    """

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


@runtime_checkable
class ResultPublisher(Protocol):
    """Sends a completed run's redacted ``RunReport`` to an external channel.

    Implementations must be side-effect-safe to call on *every* terminal run:
    the dispatch layer hands them all publishable runs and they decide whether
    (and how) to deliver. ``session`` is the dispatch DB session, so a publisher
    can read its own per-suite config (e.g. the Teams webhook + alert policy)
    without opening another. They may raise — the dispatch layer isolates failures
    so a broken channel never fails the run.
    """

    def publish(self, session: Session, report: RunReport) -> None: ...


@runtime_checkable
class HealthPublisher(Protocol):
    """Sends a connection's poll-health transition to an external channel.

    A sibling of :class:`ResultPublisher`, not a widening of it: the same channels
    (Teams · Slack · email) deliver both, but a health alert has no suite, so none of
    the per-suite machinery (`enabled`, `alert_on` threshold, per-suite webhook) applies
    — it is **workspace-level** and routes to the workspace destination only.

    *Whether* to alert is decided upstream, at the threshold crossing (`worker.tasks`);
    a publisher reaching here just delivers. Implementations may raise — the composite
    isolates a broken channel, exactly as on the run path.
    """

    def publish_health(self, session: Session, report: ConnectionHealthReport) -> None: ...


@runtime_checkable
class AlertPublisher(ResultPublisher, HealthPublisher, Protocol):
    """A channel that can deliver **both** a run outcome and a connection-health edge.

    Every real publisher (Teams/Slack/email/composite/noop) implements both halves, so
    the registry can hold one composite and serve either seam from it. The two protocols
    stay separate so a future channel can implement just one without lying about the
    other.
    """
