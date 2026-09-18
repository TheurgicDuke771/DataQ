"""Warehouse tiers — pluggable, and SKIPPED with a stated reason when no live
warehouse is reachable.

A tier that is merely absent from the output reads as "nothing to report". These
register as real cases carrying `skip_reason`, so every run emits a
``not_measured`` row naming what was not measured and why.
"""

from __future__ import annotations

from backend.scripts.perf.harness import Case, Metric, register

#: Set per deployment/harness window; a tier runs only when its gate is lifted.
_HARNESS_GATE = (
    "needs a live warehouse — harness compute is stopped by default (ADR 0021); "
    "run during a harness window and drop --skip-warehouse"
)

WAREHOUSE_TIERS: tuple[tuple[str, str, str, str], ...] = (
    ("snowflake.1m.5checks", "snowflake", "1M rows x 5 checks (pushdown)", _HARNESS_GATE),
    ("snowflake.50m.5checks", "snowflake", "50M rows x 5 checks (pushdown)", _HARNESS_GATE),
    (
        "unity_catalog.1m.5checks",
        "unity_catalog",
        "1M rows x 5 checks (SQL pushdown)",
        _HARNESS_GATE,
    ),
    (
        "unity_catalog.1m.5checks.frame",
        "unity_catalog",
        "1M rows x 5 checks (frame load, UC_SQL_PUSHDOWN=false)",
        _HARNESS_GATE,
    ),
    ("iceberg.1m.5checks", "iceberg", "1M rows x 5 checks (pyiceberg snapshot)", _HARNESS_GATE),
    (
        "profiler.snowflake.wide",
        "snowflake",
        "50-column profile (batched rank-join)",
        _HARNESS_GATE,
    ),
)


def _unreachable() -> list[Metric]:  # pragma: no cover — never called while skipped
    raise RuntimeError("warehouse tiers are not runnable without a live warehouse")


for case_id, datasource, tier, reason in WAREHOUSE_TIERS:
    register(
        Case(
            id=f"warehouse.{case_id}",
            family="warehouse_run",
            datasource=datasource,
            tier=tier,
            fn=_unreachable,
            tags=("warehouse",),
            skip_reason=reason,
        )
    )
