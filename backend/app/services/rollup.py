"""Shared result-rollup primitives — one status histogram, one score, one
latest-run-per-suite query (#889).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.db.models import RESULT_SEVERITY_TIERS, Result, Run

# ── health score (ADR 0005) ────────────────────────────────────────────────── Fixed penalty
# weights; W_MAX (the critical weight) normalises into [0, 100] so all-fail scores 50, not the
# floor — critical stays meaningfully worse than fail.
_PENALTY: Mapping[str, float] = {
    "pass": 0.0,
    "warn": 0.5,
    "fail": 1.0,
    "critical": 2.0,
}  # nosec B105
_W_MAX = 2.0

# Only the four severity tiers count toward the score / pass-rate.
SEVERITY_STATUSES: tuple[str, ...] = RESULT_SEVERITY_TIERS

# Health-score bands for the per-suite performance state label.
_OPTIMAL_MIN = 90.0
_STABLE_MIN = 60.0


def evaluated_total(counts: Mapping[str, int]) -> int:
    """How many results in ``counts`` actually evaluated a severity."""
    return sum(counts.get(s, 0) for s in SEVERITY_STATUSES)


def health_score(counts: Mapping[str, int]) -> float | None:
    """ADR-0005 health score from a status histogram, or ``None`` when no
    severity results are in scope.
    """
    n = evaluated_total(counts)
    if n == 0:
        return None
    penalty = sum(_PENALTY[s] * counts.get(s, 0) for s in SEVERITY_STATUSES)
    return round(100.0 * (1.0 - penalty / (n * _W_MAX)), 1)


def pass_rate(counts: Mapping[str, int]) -> float | None:
    """Share of evaluated (severity) results that passed, 0-100, or ``None`` when
    nothing evaluated. Excludes `skip`/`error` from the denominator (as the score).
    """
    n = evaluated_total(counts)
    if n == 0:
        return None
    return round(100.0 * counts.get("pass", 0) / n, 1)


def performance_state(score: float | None) -> str:
    """Coarse state label for a suite's health score (prototype Suite Performance)."""
    if score is None:
        return "unknown"
    if score >= _OPTIMAL_MIN:
        return "optimal"
    if score >= _STABLE_MIN:
        return "stable"
    return "critical"


# ── shared queries ───────────────────────────────────────────────────────────

#: The run statuses whose `results` rows are a **complete, final** account of what the suite found,
#: and therefore the only ones an aggregate may count.
AGGREGATABLE_RUN_STATUSES: frozenset[str] = frozenset({"succeeded"})


def latest_runs_per_suite_stmt(
    suite_scope: Select[tuple[uuid.UUID]] | Sequence[uuid.UUID],
) -> Select[Any]:
    """`SELECT DISTINCT ON (suite_id) * FROM runs …` — each suite's newest run."""
    return (
        select(Run)
        .where(Run.suite_id.in_(suite_scope))
        .order_by(Run.suite_id, Run.created_at.desc(), Run.id.desc())
        .distinct(Run.suite_id)
    )


def status_histograms(
    session: Session, run_ids: Sequence[uuid.UUID], *, complete_runs_only: bool = False
) -> dict[uuid.UUID, dict[str, int]]:
    """``run_id -> {status: count}`` for a set of runs, in one grouped query."""
    if not run_ids:
        return {}
    stmt = select(Result.run_id, Result.status, func.count()).where(Result.run_id.in_(run_ids))
    if complete_runs_only:
        stmt = stmt.join(Run, Run.id == Result.run_id).where(
            Run.status.in_(AGGREGATABLE_RUN_STATUSES)
        )
    rows = session.execute(stmt.group_by(Result.run_id, Result.status)).all()
    by_run: dict[uuid.UUID, dict[str, int]] = defaultdict(dict)
    for run_id, status, n in rows:
        by_run[run_id][status] = n
    return dict(by_run)
