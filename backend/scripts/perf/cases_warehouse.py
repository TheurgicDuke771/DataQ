"""Warehouse tiers — real runners against live warehouses, plus a fully local
Iceberg memory curve.

Every tier drives DataQ's own `CheckRunner`. A tier whose credentials are absent
registers with a `skip_reason` naming exactly which variables are missing, so a
credential-less run (CI, a laptop) emits a `not_measured` row rather than leaving
the tier silently absent — "nothing to report" and "not reported" must not look
alike.

Secrets are read from the environment at run time only. They are never logged and
never reach a metric, a tier label or the result JSON; `test_cases_warehouse`
asserts that against the emitted rows.

Environment, per tier:

  Snowflake   PERF_SF_ACCOUNT PERF_SF_USER PERF_SF_ROLE PERF_SF_DATABASE
              PERF_SF_SCHEMA PERF_SF_WAREHOUSE PERF_SF_TABLE_1M PERF_SF_TABLE_50M
              PERF_SF_WIDE_TABLE (the profiler tier)   secret: PERF_SF_SECRET
              optional, for a table the harness did not build (e.g. a sample share):
              PERF_SF_SUITE_JSON  PERF_SF_SCHEMA_1M/_50M  PERF_SF_ROWS_1M/_50M
  Unity Cat.  PERF_UC_WORKSPACE_URL PERF_UC_WAREHOUSE_ID PERF_UC_CATALOG
              PERF_UC_SCHEMA PERF_UC_TABLE_1M          secret: PERF_UC_SECRET
  Iceberg     PERF_ICEBERG_CATALOG_JSON PERF_ICEBERG_TABLE
              (optional secret: PERF_ICEBERG_SECRET)

The Iceberg *curve* (`--tag iceberg_curve`) needs none of these: it builds a local
sqlite-catalog warehouse under PERF_DATA_DIR. Generate its fixtures first with
`perf_baseline gen-iceberg --rows N`, which runs in its own process so the
generation cost is not recorded as the measurement.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from backend.scripts.perf import datagen
from backend.scripts.perf.harness import Case, Metric, register

#: The same five expectations every other rung in the baseline uses.
_CHECK_SHAPES: tuple[tuple[str, dict[str, Any]], ...] = (
    ("expect_column_values_to_not_be_null", {"column": "order_id"}),
    ("expect_column_values_to_not_be_null", {"column": "sku_id"}),
    ("expect_column_values_to_be_between", {"column": "qty", "min_value": 1, "max_value": 20}),
    (
        "expect_column_values_to_be_between",
        {"column": "unit_price", "min_value": 0, "max_value": 1000},
    ),
    ("expect_column_values_to_be_unique", {"column": "line_id"}),
)

#: The scan caps exist to REFUSE the reads the curve measures — the point is to
#: find the ceiling, which the guardrail would otherwise hide.
_UNCAPPED = {"RUN_MAX_SCAN_ROWS": "0", "RUN_MAX_SCAN_ROWS_ICEBERG": "0"}

ICEBERG_CURVE_ROWS = (1_000_000, 2_000_000, 3_000_000, 4_000_000, 5_000_000)

#: A secret NAME the harness's own store ignores — never a credential.
_SECRET_REF = "perf-secret-ref"  # noqa: S105  # nosec B105


def _check_specs(override_var: str | None = None) -> list[Any]:
    """The standard five, or — for a tier pointed at a table the harness did not
    build (a vendor sample share, say) — the JSON suite named by ``override_var``:
    ``[["expect_…", {kwargs}], …]``. Column names differ; the work should not.
    """
    import json

    from backend.app.datasources.base import CheckSpec

    shapes: Any = _CHECK_SHAPES
    raw = os.environ.get(override_var, "").strip() if override_var else ""
    if raw:
        shapes = json.loads(raw)
    return [CheckSpec(expectation_type=name, kwargs=dict(kwargs)) for name, kwargs in shapes]


def _outcome_metrics(outcome: Any) -> list[Metric]:
    """A suite that ERRORS measures the error path, not the tier — make it visible."""
    checks = list(outcome.checks)
    return [
        Metric("checks_passed", float(sum(1 for c in checks if c.success)), "checks", "observe"),
        Metric("checks_errored", float(sum(1 for c in checks if c.errored)), "checks", "exact"),
    ]


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() or None if value else None


def _missing(*names: str) -> list[str]:
    return [name for name in names if not _env(name)]


def _gate(*names: str) -> str | None:
    """The skip reason for a tier whose environment is incomplete, or ``None``."""
    missing = _missing(*names)
    if not missing:
        return None
    return f"no live warehouse configured here — missing {', '.join(missing)}"


# ───────────────────────────── instrumentation ─────────────────────────────


class _Counters:
    """What the measured work asked the warehouse for.

    `statements` is every statement any SQLAlchemy engine emitted, GX's included
    — the listener is on the Engine CLASS, because the engine a runner hands to
    GX is built inside GX and is not ours to instrument. `result_rows` sums the
    driver's own `cursor.rowcount`; a driver that does not know reports -1, which
    is counted separately rather than folded into the total as a zero.
    """

    def __init__(self) -> None:
        self.statements = 0
        self.result_rows = 0
        self.result_rows_unknown = 0
        self.frame_rows = 0


@contextmanager
def _counted() -> Iterator[_Counters]:
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    counters = _Counters()

    def before(conn: Any, cursor: Any, statement: str, *_a: Any, **_kw: Any) -> None:
        counters.statements += 1

    def after(conn: Any, cursor: Any, statement: str, *_a: Any, **_kw: Any) -> None:
        count = getattr(cursor, "rowcount", -1)
        if isinstance(count, int) and count >= 0:
            counters.result_rows += count
        else:
            counters.result_rows_unknown += 1

    event.listen(Engine, "before_cursor_execute", before)
    event.listen(Engine, "after_cursor_execute", after)
    try:
        yield counters
    finally:
        event.remove(Engine, "before_cursor_execute", before)
        event.remove(Engine, "after_cursor_execute", after)


@contextmanager
def _measured_frames(target: Any, names: tuple[str, ...], counters: _Counters) -> Iterator[None]:
    """Record rows actually materialised into the worker, at the reader seam.

    Without this, "the worker held nothing" would be an assumption restated as a
    metric; wrapping the seam makes zero a measurement. A name that is not there
    is fatal — a moved seam must not silently become "no rows were read".
    """
    missing = [name for name in names if not hasattr(target, name)]
    if missing:
        raise SeamMissingError(f"{target!r} no longer exposes {missing}")
    originals = {name: getattr(target, name) for name in names}

    def wrap(original: Callable[..., Any]) -> Callable[..., Any]:
        def measured(*args: Any, **kwargs: Any) -> Any:
            result = original(*args, **kwargs)
            frame = result[0] if isinstance(result, tuple) else result
            try:
                counters.frame_rows += len(frame)
            except TypeError:  # not a frame — nothing to attribute
                pass
            return result

        return measured

    for name, original in originals.items():
        setattr(target, name, wrap(original))
    try:
        yield
    finally:
        for name, original in originals.items():
            setattr(target, name, original)


class SeamMissingError(RuntimeError):
    """A seam this harness measures at is not where it used to be."""


def _run_metrics(
    *, elapsed: float, rows: int, checks: int, counters: _Counters, frame_seam: bool
) -> list[Metric]:
    """`frame_rows` is emitted only where a reader seam was actually wrapped.

    A pushdown lane has no frame seam, so reporting 0 there would restate the
    claim under test as its own evidence; `peak_rss_mib` (measured by the harness
    for every case) is what answers "did the worker hold the table".
    """
    metrics = [
        Metric("run_wall_s", elapsed, "s", "observe"),
        Metric("rows_per_s", rows / elapsed if elapsed else 0.0, "rows/s", "observe"),
        Metric("table_rows", float(rows), "rows", "observe"),
        Metric("checks_evaluated", float(checks), "checks", "exact"),
        Metric("statements", float(counters.statements), "statements", "strict"),
        Metric("result_rows", float(counters.result_rows), "rows", "observe"),
        Metric("result_rows_unknown", float(counters.result_rows_unknown), "statements", "observe"),
    ]
    if frame_seam:
        metrics.append(Metric("frame_rows", float(counters.frame_rows), "rows", "exact"))
    return metrics


# ───────────────────────────────── snowflake ─────────────────────────────────

_SF_ENV = (
    "PERF_SF_ACCOUNT",
    "PERF_SF_USER",
    "PERF_SF_ROLE",
    "PERF_SF_DATABASE",
    "PERF_SF_SCHEMA",
    "PERF_SF_WAREHOUSE",
    "PERF_SF_SECRET",
)


def _snowflake_config_dict() -> dict[str, Any]:
    return {
        "account": _env("PERF_SF_ACCOUNT"),
        "user": _env("PERF_SF_USER"),
        "role": _env("PERF_SF_ROLE"),
        "database": _env("PERF_SF_DATABASE"),
        "schema": _env("PERF_SF_SCHEMA"),
        "warehouse": _env("PERF_SF_WAREHOUSE"),
        "auth_type": os.environ.get("PERF_SF_AUTH_TYPE", "password"),
    }


def _snowflake_runner() -> Any:
    from backend.app.datasources.snowflake import SnowflakeCheckRunner, SnowflakeConfig

    return SnowflakeCheckRunner(
        SnowflakeConfig.model_validate(_snowflake_config_dict()), os.environ["PERF_SF_SECRET"]
    )


def _run_snowflake(table_var: str, rows: int) -> list[Metric]:
    """Snowflake pushes every expectation down as SQL and has no reader seam —
    so `frame_rows` is deliberately absent here, and `peak_rss_mib` carries the
    "the worker holds nothing" claim."""
    runner = _snowflake_runner()
    specs = _check_specs("PERF_SF_SUITE_JSON")
    tier = table_var.removeprefix("PERF_SF_TABLE_")
    schema = _env(f"PERF_SF_SCHEMA_{tier}") or _env("PERF_SF_SCHEMA")
    rows = int(_env(f"PERF_SF_ROWS_{tier}") or rows)
    try:
        with _counted() as counters:
            started = time.perf_counter()
            outcome = runner.run_checks(table=os.environ[table_var], schema=schema, checks=specs)
            elapsed = time.perf_counter() - started
    finally:
        runner.close()
    return [
        *_run_metrics(
            elapsed=elapsed,
            rows=rows,
            checks=len(outcome.checks),
            counters=counters,
            frame_seam=False,
        ),
        *_outcome_metrics(outcome),
    ]


def _profile_snowflake_wide() -> list[Metric]:
    """The post-#327 batched rank-join, which the flat-file profiler cannot stand in for."""
    import uuid

    from backend.app.db.models import Connection
    from backend.app.services import profile_service

    connection = Connection(
        id=uuid.uuid4(),
        name="perf-sf",
        type="snowflake",
        env="dev",
        config=_snowflake_config_dict(),
        # A NAME, not a credential — `_EnvSecretStore` ignores it and answers from
        # the one environment variable it was built with.
        secret_ref=_SECRET_REF,
    )
    store = _EnvSecretStore("PERF_SF_SECRET")
    table, schema = os.environ["PERF_SF_WIDE_TABLE"], _env("PERF_SF_SCHEMA")
    # Listing is NOT part of the measurement — the tier is the batched rank-join.
    columns = profile_service.list_table_columns(
        connection, table=table, schema=schema, secret_store=store
    )
    with _counted() as counters:
        started = time.perf_counter()
        result = profile_service.profile_table(
            connection,
            table=table,
            schema=schema,
            columns=columns,
            top_n=10,
            secret_store=store,
        )
        elapsed = time.perf_counter() - started
    return [
        Metric("profile_wall_s", elapsed, "s", "observe"),
        Metric("columns_profiled", float(len(result.columns)), "columns", "exact"),
        Metric("statements", float(counters.statements), "statements", "strict"),
    ]


class _EnvSecretStore:
    """A `SecretStore` that answers from ONE environment variable and cannot be
    asked for anything else — the harness never handles a secret by name.
    """

    def __init__(self, variable: str) -> None:
        self._variable = variable

    def get(self, name: str) -> str:
        return os.environ[self._variable]

    def set(self, name: str, value: str) -> None:  # pragma: no cover — never called
        raise NotImplementedError

    def delete(self, name: str) -> None:  # pragma: no cover — never called
        raise NotImplementedError


# ────────────────────────────── unity catalog ──────────────────────────────

_UC_ENV = (
    "PERF_UC_WORKSPACE_URL",
    "PERF_UC_WAREHOUSE_ID",
    "PERF_UC_CATALOG",
    "PERF_UC_SCHEMA",
    "PERF_UC_TABLE_1M",
    "PERF_UC_SECRET",
)


def _run_unity_catalog(rows: int) -> list[Metric]:
    from backend.app.datasources import unity_catalog as uc_mod

    catalog = os.environ["PERF_UC_CATALOG"]
    runner = uc_mod.UnityCatalogCheckRunner(
        config=uc_mod.UnityCatalogConfig.model_validate(
            {
                "workspace_url": _env("PERF_UC_WORKSPACE_URL"),
                "warehouse_id": _env("PERF_UC_WAREHOUSE_ID"),
            }
        ),
        token=os.environ["PERF_UC_SECRET"],
        catalog=catalog,
    )
    specs = _check_specs()
    try:
        # `_read_table` is the frame lane's own reader: wrapping it is what tells
        # the two lanes apart by MEASUREMENT rather than by the flag the case sets.
        with _counted() as counters, _measured_frames(runner, ("_read_table",), counters):
            started = time.perf_counter()
            outcome = runner.run_checks(
                table=os.environ["PERF_UC_TABLE_1M"], schema=_env("PERF_UC_SCHEMA"), checks=specs
            )
            elapsed = time.perf_counter() - started
    finally:
        runner.close()
    return _run_metrics(
        elapsed=elapsed,
        rows=rows,
        checks=len(outcome.checks),
        counters=counters,
        frame_seam=True,
    )


# ──────────────────────────────── iceberg ────────────────────────────────

_ICEBERG_ENV = ("PERF_ICEBERG_CATALOG_JSON", "PERF_ICEBERG_TABLE")


def _iceberg_metrics(config: dict[str, Any], secret: str | None, identifier: str) -> list[Metric]:
    from backend.app.datasources import iceberg as iceberg_mod

    runner = iceberg_mod.IcebergCheckRunner(
        config=iceberg_mod.IcebergConfig.model_validate(config), secret=secret
    )
    specs = _check_specs()
    planned = iceberg_mod.planned_row_count(runner._load_table(identifier))
    # A SQL catalog IS SQLAlchemy, so `statements` is genuinely instrumented here
    # — emitting it uncounted would be the zero `frame_rows` is withheld to avoid.
    with (
        _counted() as counters,
        _measured_frames(iceberg_mod, ("_to_arrow_backed_pandas",), counters),
    ):
        started = time.perf_counter()
        outcome = runner.run_checks(table=identifier, schema=None, checks=specs)
        elapsed = time.perf_counter() - started
    metrics = _run_metrics(
        elapsed=elapsed,
        rows=planned,
        checks=len(outcome.checks),
        counters=counters,
        frame_seam=True,
    )
    metrics.append(Metric("planned_rows", float(planned), "rows", "exact"))
    return metrics


def _run_iceberg_live() -> list[Metric]:
    config = json.loads(os.environ["PERF_ICEBERG_CATALOG_JSON"])
    return _iceberg_metrics(config, _env("PERF_ICEBERG_SECRET"), os.environ["PERF_ICEBERG_TABLE"])


def _run_iceberg_curve(rows: int) -> list[Metric]:
    return _iceberg_metrics(
        datagen.iceberg_connection_config(), None, datagen.iceberg_identifier(rows)
    )


def _iceberg_curve_gate(rows: int) -> str | None:
    if datagen.iceberg_table_exists(rows):
        return None
    return (
        f"local Iceberg fixture absent — build it first with "
        f"`perf_baseline gen-iceberg --rows {rows}` (its own process, so the "
        "generation cost is not recorded as the measurement)"
    )


# ──────────────────────────────── registry ────────────────────────────────


def _bind(fn: Callable[..., list[Metric]], *args: Any) -> Callable[[], list[Metric]]:
    def run() -> list[Metric]:
        return fn(*args)

    return run


def _register() -> None:
    sf_gate = _gate(*_SF_ENV)
    uc_gate = _gate(*_UC_ENV)

    for tier_key, table_var, rows in (
        ("1m", "PERF_SF_TABLE_1M", 1_000_000),
        ("50m", "PERF_SF_TABLE_50M", 50_000_000),
    ):
        register(
            Case(
                id=f"warehouse.snowflake.{tier_key}.5checks",
                family="warehouse_run",
                datasource="snowflake",
                tier=f"{tier_key.upper()} rows x 5 checks (pushdown)",
                fn=_bind(_run_snowflake, table_var, rows),
                tags=("full", "warehouse"),
                skip_reason=sf_gate or _gate(table_var),
                env=dict(_UNCAPPED),
            )
        )

    register(
        Case(
            id="warehouse.profiler.snowflake.wide",
            family="profiler",
            datasource="snowflake",
            tier="wide-table profile (batched rank-join)",
            fn=_profile_snowflake_wide,
            tags=("full", "warehouse"),
            skip_reason=sf_gate or _gate("PERF_SF_WIDE_TABLE"),
        )
    )

    for case_id, pushdown, label in (
        ("warehouse.unity_catalog.1m.5checks", "true", "1M rows x 5 checks (SQL pushdown)"),
        (
            "warehouse.unity_catalog.1m.5checks.frame",
            "false",
            "1M rows x 5 checks (frame load, UC_SQL_PUSHDOWN=false)",
        ),
    ):
        register(
            Case(
                id=case_id,
                family="warehouse_run",
                datasource="unity_catalog",
                tier=label,
                fn=_bind(_run_unity_catalog, 1_000_000),
                tags=("full", "warehouse"),
                skip_reason=uc_gate,
                env={**_UNCAPPED, "UC_SQL_PUSHDOWN": pushdown},
            )
        )

    register(
        Case(
            id="warehouse.iceberg.1m.5checks",
            family="warehouse_run",
            datasource="iceberg",
            tier="1M rows x 5 checks (pyiceberg snapshot)",
            fn=_run_iceberg_live,
            tags=("full", "warehouse"),
            skip_reason=_gate(*_ICEBERG_ENV),
            env=dict(_UNCAPPED),
        )
    )

    # The memory curve — local, credential-free, and NOT in `ci`: each rung wants
    # gigabytes and the whole point is that a rung dies.
    for rows in ICEBERG_CURVE_ROWS:
        register(
            Case(
                id=f"iceberg_curve.{rows // 1_000_000}m.5checks",
                family="iceberg_curve",
                datasource="iceberg_local",
                tier=f"{rows // 1_000_000}M rows x 5 checks (local warehouse)",
                fn=_bind(_run_iceberg_curve, rows),
                tags=("full", "iceberg_curve"),
                skip_reason=_iceberg_curve_gate(rows),
                env=dict(_UNCAPPED),
            )
        )


_register()
