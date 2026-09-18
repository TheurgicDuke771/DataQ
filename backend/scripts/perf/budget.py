"""The regression budget: compare a fresh run against the committed baseline.

Wall clock on a shared machine is not gateable — measured run-to-run variance is
recorded in the baseline itself, and every wall metric carries the ``observe``
gate, reported but never failed. What IS gated is deterministic: the statements
a read issues, the bytes and calls a runner asks the store for, the rows it
reads, and peak RSS within a band.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Tolerance per gate. `strict` counters must not grow at all; peak RSS is
#: allowed a band because allocator behaviour is not bit-reproducible.
TOLERANCES = {"strict": 0.0, "band": 0.20}


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
        return (
            f"{self.case}:{self.metric} {self.baseline:.4g} -> {self.observed:.4g} "
            f"({self.change_pct:+.1f}%, {self.gate} gate, tolerance {self.tolerance:.0%})"
        )


def _index(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(row["case"], row["metric"]): row for row in rows}


def compare(
    baseline_rows: list[dict[str, Any]],
    observed_rows: list[dict[str, Any]],
    *,
    tolerances: dict[str, float] | None = None,
    gates: set[str] | None = None,
) -> tuple[list[Violation], list[str]]:
    """Violations, plus notes for metrics that could not be compared.

    ``gates`` narrows which gates are enforced. CI enforces ``strict`` only:
    peak RSS depends on the platform's allocator and libraries, so a band taken
    on one machine says nothing on another.
    """
    limits = {**TOLERANCES, **(tolerances or {})}
    enforced = gates if gates is not None else set(TOLERANCES)
    base = _index(baseline_rows)
    fresh = _index(observed_rows)
    violations: list[Violation] = []
    notes: list[str] = []

    for key, row in fresh.items():
        gate = row.get("gate", "observe")
        if gate not in enforced or row.get("status") == "not_measured":
            continue
        baseline_row = base.get(key)
        if baseline_row is None:
            notes.append(f"{key[0]}:{key[1]} is new — no baseline to compare against")
            continue
        limit = limits.get(gate, 0.0)
        allowed = float(baseline_row["value"]) * (1 + limit)
        if float(row["value"]) > allowed:
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
            notes.append(f"{key[0]}:{key[1]} is in the baseline but was not measured in this run")
    return violations, notes
