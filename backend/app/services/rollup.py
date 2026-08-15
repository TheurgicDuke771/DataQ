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
# The keys are severity tiers (ADR 0005), not credentials — bandit's B105 flags the
# "pass": 0.0 pair as a hardcoded password purely on the key name, so the suppression
# below is load-bearing and must stay.
#
# It is deliberately BARE (#806). Bandit parses everything after the suppression token
# as a test-id list, so prose sharing that line emits one warning per word — and a
# second copy of the marker on a line outside the flagged node's range suppresses
# nothing while emitting "no failed test". This block previously had both: the
# closing-brace marker did the work, the prose copy above was inert noise. Keep the
# reason here, in prose that never spells the marker.
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

#: The run statuses whose `results` rows are a **complete, final** account of
#: what the suite found, and therefore the only ones an aggregate may count.
#:
#: This used to be true implicitly and is now enforced explicitly (#318). Result
#: rows are committed per execution phase, so a ``running`` run legitimately has
#: a partial set — a 30-check suite whose first phase failed would otherwise
#: render the dashboard and the asset score as critical (1/1 fail) for the entire
#: remainder of the run. And a ``failed``/``cancelled`` run is supposed to have
#: none at all, but the two paths that make that true are both fallible: the
#: run path's compensating DELETE is best-effort (and is issued right after the
#: DB error that failed the run), and the stuck-run reaper flips a dead worker's
#: status without owning the transaction that wrote the rows. Filtering here
#: makes both harmless instead of resting an invariant on a DELETE.
#:
#: Deliberately NOT applied to single-run surfaces — run progress, run detail,
#: a run's own lineage event, the alert for the run being alerted on. Those were
#: asked about *that run*, and hiding what it has measured so far is the opposite
#: of what they exist to show.
AGGREGATABLE_RUN_STATUSES: frozenset[str] = frozenset({"succeeded"})


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
    downstream. That is a statement about which *run* is newest, and it stays
    true: the asset view needs the failed one in order to report an operational
    error at all.

    What is no longer true is the reason this used to be safe. The previous
    wording argued that a run which wrote no results (because a hard failure
    rolled them back) is harmless to return. Since #318 a ``running`` run has a
    genuinely partial set and a reaped one can strand rows, so the *results*
    joined onto this must be filtered by `AGGREGATABLE_RUN_STATUSES` — which every
    consumer that counts them now does, rather than relying on the set being
    empty.

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
    session: Session, run_ids: Sequence[uuid.UUID], *, complete_runs_only: bool = False
) -> dict[uuid.UUID, dict[str, int]]:
    """``run_id -> {status: count}`` for a set of runs, in one grouped query.

    The single place `results` is counted by status. Everything downstream —
    checks_total/passed, worst severity, the health score, the #889 per-dimension
    scorecard — is a pure fold over this, so a new consumer adds a fold rather
    than another query with its own subtly different filters.

    ``complete_runs_only`` restricts the count to `AGGREGATABLE_RUN_STATUSES`, and
    every caller that presents the numbers as *the suite's quality* passes it
    (#318). It is opt-in rather than the default because the other caller — the
    per-run outcome column on the runs table — is describing one named run the
    user is looking at, where a mid-run "3 / 7 passed" is the honest answer and an
    empty one would be a regression of #425.

    Runs with no results are simply absent from the mapping (rather than present
    with an empty dict); callers already treat a missing entry as "nothing
    evaluated".
    """
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
