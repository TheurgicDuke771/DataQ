"""Datasource adapter seam.

Every datasource (Snowflake now; ADLS / S3 / Unity Catalog later) executes DQ
checks behind one ``CheckRunner`` interface that speaks GX-agnostic DTOs. The
GX-specific machinery lives entirely inside each adapter, so the run-service and
its tests depend only on the types here — never on Great Expectations internals.
This is also the seam that lets v1.1 swap GX for DQX on Unity Catalog (CLAUDE.md
§5) without rippling into the suite / check / result layer.

`CheckSpec` goes in (a check pulled from the DB); `CheckOutcome` comes out, one
per check, shaped to map cleanly onto the `results` table columns. Adapters
translate GX results into these DTOs; tests provide a fake `CheckRunner` and
never touch a live datasource.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class CheckSpec:
    """One expectation to evaluate, sourced from a `checks` row.

    `expectation_type` is the GX snake_case name (e.g.
    ``expect_column_values_to_not_be_null``); `kwargs` are its parameters
    (e.g. ``{"column": "id"}``). Adapters own the translation to the concrete
    GX expectation class.
    """

    expectation_type: str
    kwargs: dict[str, Any]


@dataclass(frozen=True)
class CheckOutcome:
    """Result of one check, shaped for the `results` table.

    `observed_value` / `expected_value` / `sample_failures` land in the
    matching JSONB columns. `sample_failures` may contain real data rows, so it
    is governed by the retention sweep and must only ever be logged through the
    PII-redacting structlog chain.
    """

    expectation_type: str
    success: bool
    observed_value: dict[str, Any] | None = None
    expected_value: dict[str, Any] | None = None
    sample_failures: dict[str, Any] | None = None


@dataclass(frozen=True)
class SuiteOutcome:
    """Aggregate result of running a list of checks against one table."""

    success: bool
    checks: list[CheckOutcome]


@runtime_checkable
class CheckRunner(Protocol):
    """Executes a set of checks against a single table and returns outcomes."""

    def run_checks(
        self,
        *,
        table: str,
        schema: str | None,
        checks: list[CheckSpec],
    ) -> SuiteOutcome: ...
