"""Datasource adapter seam."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

# One bound for every failing-row sample that lands in `Result.sample_failures` (#1196), and — since
# #1229.
SAMPLE_ROW_CAP = 20

# Sub-key inside a persisted `sample_failures` payload holding the capture-time, full-population
# value-signal summary (#1230).
VALUE_SIGNAL_SUMMARY_KEY = "value_signal_summary"


@dataclass(frozen=True)
class CheckSpec:
    """One expectation to evaluate, sourced from a `checks` row."""

    expectation_type: str
    kwargs: dict[str, Any]


@dataclass(frozen=True)
class CheckOutcome:
    """Result of one check, shaped for the `results` table."""

    expectation_type: str
    success: bool
    observed_value: dict[str, Any] | None = None
    expected_value: dict[str, Any] | None = None
    sample_failures: dict[str, Any] | None = None
    errored: bool = False
    error_message: str | None = None
    skipped: bool = False
    # The badness scalar a *monitor* (freshness/volume, ADR 0012) computed directly — age-hours, %
    # volume deviation.
    metric_value: float | None = None
    # How much of the dataset this check actually saw (#595), or `None` for a complete read.
    sampling: dict[str, Any] | None = None


@dataclass(frozen=True)
class MonitorSpec:
    """One monitor to evaluate (freshness/volume, ADR 0012), sourced from a `checks`
    row whose ``kind`` is a monitor kind. ``config`` is the check's JSONB config
    (e.g. ``{"column": "loaded_at"}`` / ``{"min_rows": 1000, "max_rows": 5000}``).
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
    """A datasource runner that can also evaluate **monitor** kinds by running
    scalar aggregates against the table — Snowflake / Unity Catalog (SQL) and
    Iceberg (native scan) today.
    """

    supported_monitor_kinds: frozenset[str]

    def run_monitors(
        self,
        *,
        table: str,
        schema: str | None,
        monitors: list[MonitorSpec],
    ) -> list[CheckOutcome]: ...


@runtime_checkable
class ConnectionAdapter(Protocol):
    """Per-datasource-type connection behaviour: config validation + live test."""

    def validate_config(self, raw: dict[str, Any]) -> BaseModel: ...

    def test(self, raw: dict[str, Any], secret: str | None, **extra_secrets: Any) -> None: ...


@runtime_checkable
class ExpiringCredentialAdapter(Protocol):
    """A `ConnectionAdapter` whose credential *states its own expiry* (#838)."""

    def credential_expiry(
        self, raw: dict[str, Any], secret: str, **extra_secrets: Any
    ) -> datetime | None: ...


#: Sampling strategies a run target may declare (#595).
SAMPLE_HEAD = "head"
SAMPLE_RANDOM = "random"
SAMPLING_STRATEGIES: tuple[str, ...] = (SAMPLE_HEAD, SAMPLE_RANDOM)


@dataclass(frozen=True)
class SampleSpec:
    """A validated sampling declaration from a suite's run target (#595)."""

    strategy: str
    rows: int
    seed: int | None = None


@dataclass(frozen=True)
class BatchSpec:
    """An unresolved flat-file batch selector (resolved live by `materialize_path`)."""

    prefix: str
    pattern: str
    strategy: str
    batch: str | None


@dataclass(frozen=True)
class ResolvedTarget:
    """The runner inputs a suite resolves to. ``table`` carries the file path for
    flat-file datasources; ``catalog`` is set only for Unity Catalog. ``batch`` is
    set only for a flat-file *batch* target, in which case ``table`` is empty until
    `materialize_path` lists the store and resolves the concrete path.
    """

    table: str
    schema: str | None
    catalog: str | None
    batch: BatchSpec | None = None
    sampling: SampleSpec | None = None


class TargetShapeError(ValueError):
    """A suite target is missing or malformed for its datasource type (#727)."""
