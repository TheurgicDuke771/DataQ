"""Alert dedup — fire on the *first* failure, not every subsequent run."""

from __future__ import annotations

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from backend.app.db.models import SEVERITY_RANK, Result, Run
from backend.app.services.rollup import AGGREGATABLE_RUN_STATUSES

# Dedup ranks failing checks by the single shared severity order (`SEVERITY_RANK`, #386/#655) so it
# can't drift from the rest of the alerting layer (routing, suppression) or run-outcome rollups.
_OPERATIONAL_KEY = "__run__"
_OPERATIONAL_RANK = SEVERITY_RANK["fail"]


def _failing_ranks(session: Session, run: Run) -> dict[str, int]:
    """The failing checks of ``run`` as ``{check_id: rank}`` (escalation-aware)."""
    rows = (
        session.execute(select(Result.check_id, Result.status).where(Result.run_id == run.id)).all()
        if run.status in AGGREGATABLE_RUN_STATUSES
        else []
    )
    ranks = {
        str(check_id): SEVERITY_RANK[status] for check_id, status in rows if status in SEVERITY_RANK
    }
    if not ranks and run.status == "failed":
        return {_OPERATIONAL_KEY: _OPERATIONAL_RANK}
    # A `succeeded` run with only pass/skip/error results has no signature here (empty) —
    # intentional: it isn't alert-worthy under routing (worst_severity is None → no send).
    return ranks


def _previous_terminal_run(session: Session, run: Run) -> Run | None:
    """The suite's most recent executed run before ``run`` (succeeded/failed)."""
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
    """True when ``run``'s alert repeats the suite's previous run (→ suppress)."""
    current = _failing_ranks(session, run)
    if not current:
        return False  # clean run — nothing to dedup
    previous_run = _previous_terminal_run(session, run)
    if previous_run is None:
        return False  # first-ever run of the suite → always fire
    previous = _failing_ranks(session, previous_run)
    # New alert if any failing check is worse than (or absent in) the prior run.
    return not any(rank > previous.get(key, 0) for key, rank in current.items())
