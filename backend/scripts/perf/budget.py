"""The regression budget: compare a fresh run against the committed baseline.

Wall clock on a shared machine is not gateable — measured run-to-run variance is
recorded in the baseline itself, and every wall metric carries the ``observe``
gate, reported but never failed. What IS gated is deterministic: the statements a
read issues, the calls a runner makes to the store, the work it reports doing,
and peak RSS within a band.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Tolerance per gate, and which direction fails.
#:   ``exact``  — WORK done (rows read, checks evaluated, schedules claimed).
#:                Either direction fails: doing less is a defect, not a saving.
#:   ``strict`` — COST incurred (statements, store calls). Only growth fails.
#:   ``band``   — bounded resource (peak RSS, bytes read). Growth past a margin
#:                fails; allocator and encoding differences are not regressions.
TOLERANCES = {"exact": 0.0, "strict": 0.0, "band": 0.20}
#: Gates whose metric must not move in EITHER direction.
TWO_SIDED = frozenset({"exact"})


@dataclass(frozen=True)
class Violation:
    case: str
    metric: str
    baseline: float
    observed: float
    gate: str
    tolerance: float

    @property
    def change_pct(self) -> float:
        return ((self.observed - self.baseline) / self.baseline * 100) if self.baseline else 0.0

    def describe(self) -> str:
        direction = "changed" if self.gate in TWO_SIDED else "grew"
        return (
            f"{self.case}:{self.metric} {direction} {self.baseline:.4g} -> {self.observed:.4g} "
            f"({self.change_pct:+.1f}%, {self.gate} gate, tolerance {self.tolerance:.0%})"
        )


@dataclass(frozen=True)
class Comparison:
    """The budget's verdict: what regressed, what could not be compared, and how
    many metrics were actually checked — a count of *compared* rows, so a run
    that matched nothing cannot report itself as a pass.
    """

    violations: list[Violation]
    notes: list[str]
    unmatched: list[str]
    compared: int

    @property
    def ok(self) -> bool:
        return not self.violations and not self.unmatched


def _index(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(row["case"], row["metric"]): row for row in rows}


def _breaches(gate: str, baseline: float, observed: float, limit: float) -> bool:
    if gate in TWO_SIDED:
        return abs(observed - baseline) > abs(baseline) * limit
    return observed > baseline * (1 + limit)


def compare(
    baseline_rows: list[dict[str, Any]],
    observed_rows: list[dict[str, Any]],
    *,
    tolerances: dict[str, float] | None = None,
    gates: set[str] | None = None,
) -> Comparison:
    """Compare a fresh run against a baseline.

    ``gates`` narrows which gates are enforced. CI enforces everything except
    ``band``: peak RSS and encoded byte counts depend on the platform's
    allocator and library versions, so a margin taken on one machine says
    nothing on another.
    """
    limits = {**TOLERANCES, **(tolerances or {})}
    enforced = gates if gates is not None else set(TOLERANCES)
    base = _index(baseline_rows)
    fresh = _index(observed_rows)
    violations: list[Violation] = []
    notes: list[str] = []
    unmatched: list[str] = []
    compared = 0

    for key, row in fresh.items():
        gate = row.get("gate", "observe")
        if gate not in enforced or row.get("status") == "not_measured":
            continue
        baseline_row = base.get(key)
        if baseline_row is None:
            # NOT a pass: a renamed case would otherwise compare nothing and
            # still print "budget OK".
            unmatched.append(f"{key[0]}:{key[1]} has no baseline row to compare against")
            continue
        compared += 1
        limit = limits.get(gate, 0.0)
        if _breaches(gate, float(baseline_row["value"]), float(row["value"]), limit):
            violations.append(
                Violation(
                    case=key[0],
                    metric=key[1],
                    baseline=float(baseline_row["value"]),
                    observed=float(row["value"]),
                    gate=gate,
                    tolerance=limit,
                )
            )

    # Only for cases this run actually exercised: a narrowed selection is not a
    # missing metric, and saying so for every unselected case buries the real one.
    ran = {case for case, _ in fresh}
    for key, row in base.items():
        if row.get("gate", "observe") not in enforced or row.get("status") == "not_measured":
            continue
        if key[0] in ran and key not in fresh:
            unmatched.append(f"{key[0]}:{key[1]} is in the baseline but was not measured")
    return Comparison(violations=violations, notes=notes, unmatched=unmatched, compared=compared)
