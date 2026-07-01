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

from pydantic import BaseModel


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

    `errored` marks a check that could not be *evaluated* (the runner caught an
    exception while computing it — e.g. it references a missing column), as
    opposed to a check that evaluated and *failed* (`success=False`). The two are
    distinct result statuses (#122): an errored check maps to ``error`` (no
    severity, no metric), a failed one to a severity tier. A single errored check
    never fails its siblings — they still evaluate and persist.
    """

    expectation_type: str
    success: bool
    observed_value: dict[str, Any] | None = None
    expected_value: dict[str, Any] | None = None
    sample_failures: dict[str, Any] | None = None
    errored: bool = False
    error_message: str | None = None
    # The badness scalar a *monitor* (freshness/volume, ADR 0012) computed directly
    # — age-hours, % volume deviation. `severity.extract_metric` prefers this when
    # set, so monitor kinds band the same way (higher = worse, ADR 0016) without
    # abusing the GX unexpected-% sample shape. None for GX expectations, whose
    # metric is parsed from the sample.
    metric_value: float | None = None


@dataclass(frozen=True)
class MonitorSpec:
    """One monitor to evaluate (freshness/volume, ADR 0012), sourced from a `checks`
    row whose ``kind`` is a monitor kind. ``config`` is the check's JSONB config
    (e.g. ``{"column": "loaded_at"}`` / ``{"min_rows": 1000, "max_rows": 5000}``).

    A monitor isn't a GX expectation — it runs a scalar SQL aggregate — so it has its
    own spec/runner path distinct from `CheckSpec`/`CheckRunner`.
    """

    kind: str
    config: dict[str, Any]


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
        index_columns: list[str] | None = None,
    ) -> SuiteOutcome: ...


@runtime_checkable
class MonitorRunner(Protocol):
    """A datasource runner that can also evaluate **monitor** kinds (freshness/
    volume) by running scalar SQL aggregates against the table — the SQL datasources
    (Snowflake, Unity Catalog) in v1. Flat-file runners don't implement this, so the
    run path can gate monitor checks to SQL datasources via an ``isinstance`` check.

    One ``CheckOutcome`` per ``MonitorSpec``, in order. A monitor that can't be
    evaluated (bad column, type mismatch) yields an ``errored`` outcome rather than
    failing its siblings — mirroring `CheckRunner` semantics.
    """

    def run_monitors(
        self,
        *,
        table: str,
        schema: str | None,
        monitors: list[MonitorSpec],
    ) -> list[CheckOutcome]: ...


@runtime_checkable
class ConnectionAdapter(Protocol):
    """Per-datasource-type connection behaviour: config validation + live test.

    The two things that vary across connection types (Snowflake now; ADF, ADLS,
    S3, Unity Catalog next) behind one interface, so connection-CRUD service code
    dispatches by ``connection.type`` and never branches on it. Each adapter owns
    its own pydantic config model; both methods take the raw config dict so the
    adapter is the single source of truth for that type's shape.

    `validate_config` parses + validates a stored/incoming config (raising
    pydantic ``ValidationError`` on bad input) and returns the normalised model.
    `test` resolves connectivity against the live datasource using the config +
    its secret, raising on failure. Adapters never touch the DB or SecretStore —
    the caller resolves the secret and hands it in.
    """

    def validate_config(self, raw: dict[str, Any]) -> BaseModel: ...

    def test(self, raw: dict[str, Any], secret: str) -> None: ...
