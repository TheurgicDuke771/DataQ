"""Run comparison — the diff of two runs of one suite (per-check status and
metric deltas, checks present on one side only), each side rendered as the
check WAS at that run's time.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.db.models import RESULT_SEVERITY_TIERS, SEVERITY_RANK, Check, Result, Run, Suite
from backend.app.services import audit_service
from backend.app.services import run_service as svc

StatusChange = Literal["improved", "regressed", "changed", "same", "added", "removed"]


class RunsNotComparableError(DataQError):
    """The two runs cannot be diffed — different suites, or one run twice."""

    status_code = 422
    code = "runs_not_comparable"


# The severity tiers in rank order (`pass` = 0, then `SEVERITY_RANK`); `skip`/`error` have no
# rank — an operational status is not a data-quality verdict, so a transition through one is
# `changed`, never improved or regressed.
_STATUS_RANK: dict[str, int] = {tier: rank for rank, tier in enumerate(RESULT_SEVERITY_TIERS)}
assert _STATUS_RANK[RESULT_SEVERITY_TIERS[0]] == 0 and all(  # nosec B101 — import-time invariant
    _STATUS_RANK[tier] == rank for tier, rank in SEVERITY_RANK.items()
)


def status_change(base: str | None, target: str | None) -> StatusChange:
    if base is None:
        return "added"
    if target is None:
        return "removed"
    if base == target:
        return "same"
    base_rank, target_rank = _STATUS_RANK.get(base), _STATUS_RANK.get(target)
    if base_rank is None or target_rank is None:
        return "changed"
    return "regressed" if target_rank > base_rank else "improved"


def _number(value: Decimal | float | None) -> float | None:
    return None if value is None else float(value)


def metric_delta(base: float | None, target: float | None) -> tuple[float | None, float | None]:
    """`(absolute, percent)` of target over base; percent only when base is non-zero."""
    if base is None or target is None:
        return None, None
    absolute = target - base
    percent = (absolute / abs(base)) * 100 if base != 0 else None
    return absolute, percent


@dataclass(frozen=True)
class CheckIdentity:
    name: str | None
    kind: str | None
    engine: str
    expectation_type: str | None
    dimension: str | None
    column: str | None
    #: See `run_service.CheckAsOfSource` (#1613).
    resolved_from: svc.CheckAsOfSource | None


@dataclass(frozen=True)
class ResultSide:
    result_id: uuid.UUID
    status: str
    metric_value: float | None
    observed_value: dict[str, Any] | None
    check: CheckIdentity


@dataclass(frozen=True)
class Delta:
    """`metric_changed` is True whenever the two sides' metrics differ — INCLUDING when only one
    side carries one, where `metric_delta`/`metric_delta_pct` stay None because no difference
    can be stated. A row with both sides, `metric_changed` and no delta is that case."""

    status_change: StatusChange
    metric_delta: float | None
    metric_delta_pct: float | None
    metric_changed: bool


@dataclass(frozen=True)
class ComparisonRow:
    check_id: uuid.UUID
    label: str
    base: ResultSide | None
    target: ResultSide | None
    delta: Delta


@dataclass(frozen=True)
class RunComparison:
    base_results_final: bool
    target_results_final: bool
    rows: list[ComparisonRow]
    summary: dict[str, int]


def _sides(
    db: Session,
    run: Run,
    suite: Suite,
    results: list[Result],
    as_of: Mapping[uuid.UUID, svc.CheckAsOf],
) -> dict[uuid.UUID, ResultSide]:
    """One redacted side per check for a run — the LAST result wins when a check has several."""
    policy = suite.column_policy
    tags = svc.asset_column_tags(db, suite, run)
    sides: dict[uuid.UUID, ResultSide] = {}
    for result in results:
        snap = as_of[result.id]
        sides[result.check_id] = ResultSide(
            result_id=result.id,
            status=result.status,
            metric_value=_number(result.metric_value),
            # The same redaction path the run-detail read uses — never a raw observed sample.
            observed_value=svc.redact_observed_value(
                result.observed_value,
                tested_column=snap.column,
                expectation_type=snap.expectation_type,
                policy=policy,
                tags=tags,
            ),
            check=CheckIdentity(
                name=snap.name,
                kind=snap.kind,
                engine=snap.engine,
                expectation_type=snap.expectation_type,
                dimension=snap.dimension,
                column=snap.column,
                resolved_from=snap.resolved_from,
            ),
        )
    return sides


def _record_access(db: Session, actor: Any, run: Run, sides: dict[uuid.UUID, ResultSide]) -> None:
    exposed = [
        str(s.result_id)
        for s in sides.values()
        if svc.observed_value_exposes_cells(
            s.observed_value, expectation_type=s.check.expectation_type
        )
    ]
    audit_service.record_access(
        db,
        action="run_results.read",
        entity_type="run",
        entity_id=run.id,
        actor=actor,
        exposed=bool(exposed),
        detail={
            "suite_id": str(run.suite_id),
            "result_count": len(sides),
            "exposed_result_ids": exposed,
            "surface": "run_comparison",
        },
    )


def compare_runs(db: Session, *, base: Run, target: Run, suite: Suite, actor: Any) -> RunComparison:
    """Diff `target` against `base`. Both runs must belong to `suite` and be distinct;
    the caller has already applied the suite-visibility gate to both.
    """
    if base.id == target.id:
        raise RunsNotComparableError(
            "a run cannot be compared with itself", detail={"run_id": str(base.id)}
        )
    if base.suite_id != suite.id or target.suite_id != suite.id:
        raise RunsNotComparableError(
            "the two runs belong to different suites",
            detail={"base_suite_id": str(base.suite_id), "target_suite_id": str(target.suite_id)},
        )
    checks = {c.id: c for c in db.scalars(select(Check).where(Check.suite_id == suite.id))}
    base_results = svc.list_results(db, base.id)
    target_results = svc.list_results(db, target.id)
    # One `check_versions` read for both runs — they share the suite's check-id set.
    as_of = svc.historical_check_snapshots(db, base_results + target_results, checks)
    base_sides = _sides(db, base, suite, base_results, as_of)
    target_sides = _sides(db, target, suite, target_results, as_of)
    _record_access(db, actor, base, base_sides)
    _record_access(db, actor, target, target_sides)

    rows: list[ComparisonRow] = []
    for check_id in set(base_sides) | set(target_sides):
        b, t = base_sides.get(check_id), target_sides.get(check_id)
        change = status_change(b.status if b else None, t.status if t else None)
        b_metric = b.metric_value if b else None
        t_metric = t.metric_value if t else None
        absolute, percent = metric_delta(b_metric, t_metric)
        side_for_label = t or b
        assert side_for_label is not None  # nosec B101 — the union guarantees a side
        rows.append(
            ComparisonRow(
                check_id=check_id,
                label=side_for_label.check.name or str(check_id)[:8],
                base=b,
                target=t,
                delta=Delta(
                    status_change=change,
                    metric_delta=absolute,
                    metric_delta_pct=percent,
                    metric_changed=b is not None and t is not None and b_metric != t_metric,
                ),
            )
        )
    rows.sort(key=lambda r: (r.label.casefold(), str(r.check_id)))

    changes = [r.delta.status_change for r in rows]
    summary = {
        "changed": sum(c in ("improved", "regressed", "changed") for c in changes),
        "unchanged": changes.count("same"),
        "improved": changes.count("improved"),
        "regressed": changes.count("regressed"),
        "added": changes.count("added"),
        "removed": changes.count("removed"),
        "metric_changed": sum(r.delta.metric_changed for r in rows),
    }
    return RunComparison(
        base_results_final=svc.results_final(base),
        target_results_final=svc.results_final(target),
        rows=rows,
        summary=summary,
    )
