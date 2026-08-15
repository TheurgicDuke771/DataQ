"""Collect `run_service._run_outcome_phases` into one outcome-per-check list.

This used to be `run_service._run_outcomes`, a production function with no
production callers: once `execute_run` started consuming the phases directly
(#318) the whole-suite view existed only for these tests, and a helper that
exists for tests belongs with the tests — otherwise the coverage it earns is
reporting on itself.

It keeps what the tests actually assert on: **check order**. The phases are
yielded in execution order, which is deliberately not check order (the stateful
kinds run last, see `_run_outcome_phases`), so the fill-by-index here is what
lets a test say "outcome 1 belongs to check 1".
"""

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
