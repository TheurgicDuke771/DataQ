"""Collect `run_service._run_outcome_phases` into one outcome-per-check list."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from backend.app.datasources.base import CheckOutcome, CheckRunner
from backend.app.db.models import Check
from backend.app.services import run_service


def collect_outcomes(
    runner: CheckRunner,
    *,
    table: str,
    schema: str | None,
    checks: list[Check],
    index_columns: list[str] | None = None,
    comparison_executor: Callable[[Check], CheckOutcome] | None = None,
    stateful_monitor_executor: Callable[[Check], CheckOutcome] | None = None,
) -> list[CheckOutcome]:
    """Every check's outcome, in **check** order."""
    outcomes: list[CheckOutcome | None] = [None] * len(checks)
    for phase in run_service._run_outcome_phases(
        runner,
        table=table,
        schema=schema,
        checks=checks,
        index_columns=index_columns,
        comparison_executor=comparison_executor,
        stateful_monitor_executor=stateful_monitor_executor,
    ):
        for i, outcome in phase.resolved:
            outcomes[i] = outcome
    # Every index is filled: the phases together cover all checks once the
    # unsupported-kind guard inside the generator has run.
    return [cast(CheckOutcome, oc) for oc in outcomes]
