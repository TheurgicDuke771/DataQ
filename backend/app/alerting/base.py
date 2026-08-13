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


class AlertUndeliverableError(RuntimeError):
    """Raised by the composite when a workspace-level edge reached NO channel —
    every channel either failed or quietly skipped as unconfigured. Callers doing
    #843 delivered-first bookkeeping catch this to leave the flag unset (retry next
    tick) instead of recording a delivery that never happened."""


# #1226: when EVERY channel fails, the composite's fan-out logs each failing
# channel with a full traceback (`log.exception`) before re-raising the LAST one
# so the caller can tell "genuinely undelivered" from "delivered". Without this
# marker, the caller's own `except Exception: log.exception(...)` logs that same
# last channel's traceback a second time — doubling log volume on exactly the
# correlated-outage edge case #852/the 2026-07-13 exporter-loop incident warns
# against. A plain attribute (not a wrapper exception type) so `isinstance`/type
# checks on the original exception, and any exception-message classification
# (#902), are untouched.
#
# #1261: the downgrade decision itself (check this marker, log at `warning`
# instead of `exception`) no longer lives in each caller — it moved to a shared
# structlog processor (`_downgrade_already_logged_exceptions`, `core/logging.py`)
# so a THIRD caller gets it automatically instead of needing its own copy of the
# same `if was_already_logged(exc): ...` check. This module still owns only the
# marker itself (`mark_already_logged`/`was_already_logged`) — the composite sets
# it, the processor reads it; no caller touches it directly anymore.
#
# Caveat: the marker rides on the exception OBJECT, not on `__cause__`/`__context__`
# chaining, so it does not survive being wrapped (`raise SomeError(...) from exc`)
# between the composite's `raise last_error` and wherever it's finally logged. No
# current caller re-wraps it — verified by `/code-review` on #1260 — but a future
# error-classification or retry layer inserted in between would need to propagate
# the marker onto its own wrapper explicitly, or this bug quietly reappears. This
# now also bounds the processor: its `sys.exc_info()` fallback (for the `exc_info=
# True` shape `log.exception()` sets) only ever sees the exception CURRENTLY being
# handled, so a wrapper raised in between is exactly as invisible to the processor
# as it was to the old per-caller check.
_ALREADY_LOGGED_ATTR = "_dataq_alerting_already_logged"


def mark_already_logged(exc: BaseException) -> None:
    """Tag ``exc`` as already logged with a full traceback by the composite
    fan-out, so the logging processor chain can downgrade a later
    ``log.exception(...)`` on it to a ``warning`` instead of re-logging the same
    traceback (#1226, centralized in the chain by #1261)."""
    setattr(exc, _ALREADY_LOGGED_ATTR, True)


def was_already_logged(exc: BaseException) -> bool:
    """Whether ``exc`` was already logged with a full traceback by the composite
    fan-out (see :func:`mark_already_logged`). Read by the structlog processor
    chain (`core.logging._downgrade_already_logged_exceptions`, #1261), not by
    callers directly — nothing in ``backend/app`` should need to import this
    beyond the composite (which sets it)."""
    return bool(getattr(exc, _ALREADY_LOGGED_ATTR, False))


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


@dataclass(frozen=True)
class PollStalenessReport:
    """Workspace-wide orchestration-poll staleness (#1052) — the signal that cannot lie.

    Deliberately **not** a ``ConnectionHealthReport``: this is not a property of any
    connection. Every incident in the #905 class (#852 exporter starvation, #854
    row-lock wait, a wedged broker reconnect) had a worker that looked alive and wrote
    nothing — so a per-connection edge computed from worker writes structurally cannot
    fire. This report is derived from the DB alone (``max(last_polled_at)`` across all
    orchestration connections) and published from the API process, and its card must
    say "the polling loop is dead", not "a connection is failing".

    ``most_recent_polled_at`` is the workspace's freshest poll write — ``None`` when
    no connection has ever been polled (the reference moment is then the oldest
    connection's creation). ``threshold_seconds`` is carried so the card can say what
    "stale" meant when the edge fired. ``None`` reason fields on recovery, as on the
    connection-health edge.
    """

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

    def publish_health(self, session: Session, report: ConnectionHealthReport) -> bool:
        """Deliver a connection's poll-health edge (#837); return whether a message
        actually left this process (``False`` = quietly skipped, e.g. the channel is
        unconfigured — a skip must never read as delivered, #1101).

        The per-connection caller (``worker.tasks.publish_connection_health``) claims
        ``connections.health_alerted_at`` with a conditional UPDATE *before* dispatching
        the send (#842/#843), so this method's honesty is what tells it whether to keep
        the claim or release it for the next sweep to retry.

        At the COMPOSITE level this raises when **nothing was sent** — every channel
        failed (the last error) or every channel skipped
        (:class:`AlertUndeliverableError`) — the same delivered-first hinge as
        :meth:`publish_poll_staleness` below. A total non-delivery recorded as
        delivered would permanently suppress the edge: on a fresh install with zero
        channels configured, the still-outstanding incident would never fire even
        after an operator later wires up Slack. A partial delivery (one channel down,
        another delivered) still returns normally.
        """
        ...

    def publish_poll_staleness(self, session: Session, report: PollStalenessReport) -> bool:
        """Deliver the workspace-wide poll-staleness edge (#1052); return whether a
        message actually left this process (``False`` = quietly skipped, e.g. the
        channel is unconfigured — a skip must never read as delivered).

        On the same seam as ``publish_health`` (same channels, same workspace-level
        routing, same "whether was decided upstream" contract) rather than a parallel
        mechanism — the caller is ``workspace_health_service``, which owns the
        threshold decision and the #843 delivered-first bookkeeping.

        Shares the exact delivered-first contract with ``publish_health`` at the
        COMPOSITE level (#1101 brought the two into line): both raise when **nothing
        was sent** — every channel failed (the last error) or every channel skipped
        (:class:`AlertUndeliverableError`). The caller records "delivered" only on a
        normal return, and a total non-delivery recorded as delivered would silence
        the one alert whose whole point is to fire when everything else is silent. A
        partial failure (one channel down, another delivered) still returns normally.
        """
        ...


@runtime_checkable
class AlertPublisher(ResultPublisher, HealthPublisher, Protocol):
    """A channel that can deliver **both** a run outcome and a connection-health edge.

    Every real publisher (Teams/Slack/email/composite/noop) implements both halves, so
    the registry can hold one composite and serve either seam from it. The two protocols
    stay separate so a future channel can implement just one without lying about the
    other.
    """
