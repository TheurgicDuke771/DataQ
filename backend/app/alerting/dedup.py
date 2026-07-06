"""Alert dedup — fire on the *first* failure, not every subsequent run.

A failing check on a scheduled suite would otherwise alert on every run while it
stays broken. This compares a run's failing checks to the suite's **previous
terminal run** and suppresses the alert when nothing got worse: a new alert
fires only when a check starts failing (or escalates severity) relative to last
time. Recovery (a clean run) resets the baseline, so the next failure re-fires.

Derived from run history — **no new state / migration**. The unit is per-check
(by ``check_id``, not name, so a renamed check still dedups), which is why this
reads ``results`` directly rather than the name-only ``RunReport``.
"""

from __future__ import annotations

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from backend.app.alerting.base import FAILING_TIERS
from backend.app.db.models import Result, Run

# Failing severity tiers → rank (higher = worse), derived from the single shared
# severity order in `alerting.base.FAILING_TIERS` (#386) so dedup can't silently
# drift from the rest of the alerting layer (routing, suppression) when a tier is
# added or reordered. `pass`/`skip`/`error` aren't alert-worthy and never appear here.
_RANK = {tier: rank for rank, tier in enumerate(FAILING_TIERS, start=1)}
# An operational run failure (the adapter raised — no per-check result rows) is a
# single suite-level failure signature, keyed by this sentinel, ranked at `fail`.
_OPERATIONAL_KEY = "__run__"
_OPERATIONAL_RANK = _RANK["fail"]


def _failing_ranks(session: Session, run: Run) -> dict[str, int]:
    """The failing checks of ``run`` as ``{check_id: rank}`` (escalation-aware).

    An executed run contributes its breaching per-check results; a run that
    *failed to execute* (no results) contributes one suite-level signature so two
    consecutive operational failures still dedup.
    """
    rows = session.execute(
        select(Result.check_id, Result.status).where(Result.run_id == run.id)
    ).all()
    ranks = {str(check_id): _RANK[status] for check_id, status in rows if status in _RANK}
    if not ranks and run.status == "failed":
        return {_OPERATIONAL_KEY: _OPERATIONAL_RANK}
    # A `succeeded` run with only pass/skip/error results has no signature here
    # (empty) — intentional: it isn't alert-worthy under routing (worst_severity
    # is None → no send), so there's nothing to dedup.
    return ranks


def _previous_terminal_run(session: Session, run: Run) -> Run | None:
    """The suite's most recent executed run before ``run`` (succeeded/failed).

    Ordered by ``(created_at, id)`` as a **total** order: ``created_at`` is
    ``func.now()`` (transaction-start time), so a burst can give two runs the same
    timestamp — a strict ``created_at <`` alone would drop a same-timestamp prior
    run and pick the wrong baseline. The row-value comparison excludes ``run``
    itself while still seeing an equal-timestamp, lower-id predecessor.
    """
    return session.scalars(
        select(Run)
        .where(
            Run.suite_id == run.suite_id,
            Run.status.in_(("succeeded", "failed")),
            tuple_(Run.created_at, Run.id) < (run.created_at, run.id),
        )
        .order_by(Run.created_at.desc(), Run.id.desc())
        .limit(1)
    ).first()


def is_duplicate_alert(session: Session, run: Run) -> bool:
    """True when ``run``'s alert repeats the suite's previous run (→ suppress).

    Returns ``False`` (i.e. *do alert*) when:
    - the run has no failures (not an alert at all — the publisher no-ops it),
    - it's the suite's first terminal run (no baseline), or
    - any failing check is **new or escalated** vs the previous run.

    Returns ``True`` only when every current failure was already present at the
    same-or-higher severity last run — an ongoing, unchanged failure.
    """
    current = _failing_ranks(session, run)
    if not current:
        return False  # clean run — nothing to dedup
    previous_run = _previous_terminal_run(session, run)
    if previous_run is None:
        return False  # first-ever run of the suite → always fire
    previous = _failing_ranks(session, previous_run)
    # New alert if any failing check is worse than (or absent in) the prior run.
    return not any(rank > previous.get(key, 0) for key, rank in current.items())
