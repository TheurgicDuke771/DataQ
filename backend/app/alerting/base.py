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
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from backend.app.db.models import SEVERITY_RANK

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# Severity tiers that count as "not clean", worst last. A run is alert-worthy
# when any check lands in one of these (or the run failed to execute). `pass` is
# clean; `skip`/`error` are operational, not data-quality severities (ADR 0005),
# so they never set `worst_severity`. Derived from the shared `SEVERITY_RANK`
# (#655) so the failing-tier set and the rank order have one source.
FAILING_TIERS: tuple[str, ...] = tuple(SEVERITY_RANK)


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
