"""Shared result-rollup primitives — one status histogram, one score, one
latest-run-per-suite query (#889).

Three consumers aggregate `results` into a verdict: the dashboard (grant-scoped,
windowed), the asset view (workspace-true, ADR 0037), and the asset DQ scorecard
(#889). Before this module the plumbing existed twice and the score once, so
adding the scorecard would have made it three and two. This is the shared floor:

* :func:`status_histograms` — ``run_id -> {status: count}``, the one grouped query
  over `results`. Every other aggregate is a fold over its output, so nothing else
  needs to know how results are counted.
* :func:`health_score` / :func:`pass_rate` / :func:`performance_state` — the
  ADR-0005 math, moved here from `dashboard_service` so the scorecard imports a
  shared helper rather than reaching into the dashboard (which would be the
  "third formula" smell even when it is literally the same function).
* :func:`latest_runs_per_suite_stmt` — the DISTINCT ON both services had their own
  copy of.

**Scope-agnostic on purpose.** Neither authz posture lives here: the dashboard
injects a grant-scoped `Select` and the asset view injects a list of suite ids,
each at exactly one call site. Pushing either rule down would force the other to
inherit it — and ADR 0037 requires the asset aggregate be workspace-true while the
dashboard must stay grant-scoped.

**What this module deliberately does NOT unify:** `_PENALTY` here and
`models.SEVERITY_RANK` stay separate concepts, as both modules already documented
before this refactor. `SEVERITY_RANK` is a discrete "which outcome is worse"
ordering over the *failing* tiers; `_PENALTY` is a continuous weight that also
scores `pass`. Collapsing them would be a merge of two things that only look
alike.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.db.models import RESULT_SEVERITY_TIERS, Result, Run

# ── health score (ADR 0005) ──────────────────────────────────────────────────
# Fixed penalty weights; W_MAX (the critical weight) normalises into [0, 100] so
# all-fail scores 50, not the floor — critical stays meaningfully worse than fail.
# Deliberately separate from `db.models.SEVERITY_RANK` (#655) — see the module
# docstring.
# nosec B105 — the keys are severity tiers (ADR 0005), not credentials; bandit
# flags the "pass": 0.0 pair as a "hardcoded password" purely on the key name.
_PENALTY: Mapping[str, float] = {
    "pass": 0.0,
    "warn": 0.5,
    "fail": 1.0,
    "critical": 2.0,
}  # nosec B105
_W_MAX = 2.0

# Only the four severity tiers count toward the score / pass-rate. `skip` and
# `error` did not evaluate a severity, so they are excluded from N rather than
# treated as a pass (ADR 0005 covers the four tiers only).
#
# Imported from the model vocabulary rather than re-derived from `_PENALTY`'s
# keys: the two were the same tuple by coincidence, and a weight added here
# without a matching tier would have silently widened N.
SEVERITY_STATUSES: tuple[str, ...] = RESULT_SEVERITY_TIERS

# Health-score bands for the per-suite performance state label.
_OPTIMAL_MIN = 90.0
_STABLE_MIN = 60.0


def evaluated_total(counts: Mapping[str, int]) -> int:
    """How many results in ``counts`` actually evaluated a severity.

    The shared denominator: `skip`/`error` are excluded, so an all-skip run has a
    total of 0 and reports "—" rather than a misleading green 0/N (#122).
    """
    return sum(counts.get(s, 0) for s in SEVERITY_STATUSES)


def health_score(counts: Mapping[str, int]) -> float | None:
    """ADR-0005 health score from a status histogram, or ``None`` when no
    severity results are in scope.

    ``100 * (1 - penalty_sum / (N * 2.0))`` over the four tiers. 100 = all pass,
    0 = all critical, 50 = all fail, 75 = all warn; ``{fail, fail, pass, pass}``
    -> 75.0. Rounded to 1 dp for display stability.
    """
    n = evaluated_total(counts)
    if n == 0:
        return None
    penalty = sum(_PENALTY[s] * counts.get(s, 0) for s in SEVERITY_STATUSES)
    return round(100.0 * (1.0 - penalty / (n * _W_MAX)), 1)


def pass_rate(counts: Mapping[str, int]) -> float | None:
    """Share of evaluated (severity) results that passed, 0-100, or ``None`` when
    nothing evaluated. Excludes `skip`/`error` from the denominator (as the score)."""
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


def latest_runs_per_suite_stmt(
    suite_scope: Select[tuple[uuid.UUID]] | Sequence[uuid.UUID],
) -> Select[Any]:
    """`SELECT DISTINCT ON (suite_id) * FROM runs …` — each suite's newest run.

    ``suite_scope`` is whatever bounds the suites: a grant-scoped `Select` (the
    dashboard, never materialised) or a list of ids (the asset page, already
    bounded). Both flow into the same `IN`, so the caller keeps its authz posture
    and this stays scope-agnostic.

    Returns a **statement**, not rows, because the two consumers need different
    things from it: the dashboard keeps it in SQL and joins `results` onto it, the
    asset view materialises `Run` entities. One query shape, two uses.

    **No status or time filter** — the latest run counts whether it succeeded,
    failed, was cancelled, or is still queued; callers that want otherwise filter
    downstream. In particular a run that wrote no results (a hard failure rolls
    them back) is still returned here: the dashboard drops it with an inner join,
    the asset view keeps it to report an operational error. Encoding either
    choice here would silently change the other.

    The ``id`` tie-break is new (#889): both previous copies ordered only by
    ``created_at DESC``, so two runs on one suite sharing a timestamp resolved
    nondeterministically — the same page could show different numbers on refresh.
    """
    return (
        select(Run)
        .where(Run.suite_id.in_(suite_scope))
        .order_by(Run.suite_id, Run.created_at.desc(), Run.id.desc())
        .distinct(Run.suite_id)
    )


def status_histograms(
    session: Session, run_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, dict[str, int]]:
    """``run_id -> {status: count}`` for a set of runs, in one grouped query.

    The single place `results` is counted by status. Everything downstream —
    checks_total/passed, worst severity, the health score, the #889 per-dimension
    scorecard — is a pure fold over this, so a new consumer adds a fold rather
    than another query with its own subtly different filters.

    Runs with no results are simply absent from the mapping (rather than present
    with an empty dict); callers already treat a missing entry as "nothing
    evaluated".
    """
    if not run_ids:
        return {}
    rows = session.execute(
        select(Result.run_id, Result.status, func.count())
        .where(Result.run_id.in_(run_ids))
        .group_by(Result.run_id, Result.status)
    ).all()
    by_run: dict[uuid.UUID, dict[str, int]] = defaultdict(dict)
    for run_id, status, n in rows:
        by_run[run_id][status] = n
    return dict(by_run)
