"""The `aggregate` monitor kind (#1602): config + two-sided bands, the per-dialect SQL, the
driver-boundary scalar, and the frame path's SQL-equivalent NULL semantics. No DB."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pandas as pd
import pyarrow as pa
import pytest

from backend.app.datasources import aggregate_sql
from backend.app.datasources.monitors import (
    AGGREGATE,
    AGGREGATES,
    MonitorConfigError,
    aggregate_of_series,
    aggregate_params,
    build_monitor_statement,
    monitor_outcome,
)
from backend.app.datasources.sql_engines import SQL_ENGINES
from backend.app.services.severity import resolve_status

_NOW = datetime(2026, 9, 29, tzinfo=UTC)


def _cfg(**overrides: Any) -> dict[str, Any]:
    return {"aggregate": "mean", "column": "amount", **overrides}


def _dialects() -> dict[str, Any]:
    from sqlalchemy import make_url

    # The installed dialect each connection type's engine is built with (drivernames as in the
    # engine specs), loaded through SQLAlchemy's own registry.
    drivers = {
        "postgresql": "postgresql+psycopg2",
        "mysql": "mysql+pymysql",
        "mssql": "mssql+pytds",
        "snowflake": "snowflake",
        "databricks": "databricks",
        "trino": "trino",
        "awsathena": "awsathena+rest",
        "redshift": "redshift+psycopg2",
    }
    return {name: make_url(f"{driver}://").get_dialect()() for name, driver in drivers.items()}


def _sql(aggregate: str, dialect: Any, column: str = "amount") -> str:
    statement = build_monitor_statement(
        AGGREGATE,
        table="orders",
        schema="sales",
        catalog=None,
        config=_cfg(aggregate=aggregate, column=column, min_value=0),
        dialect=dialect,
    )
    return " ".join(str(statement.compile(dialect=dialect)).split())


# ───────────────────────────── config ─────────────────────────────


def test_params_keep_only_the_bounds_that_are_set() -> None:
    params = aggregate_params(_cfg(min_value=1, max_value=10.5, warn_max=None))
    assert params.bounds == {"min_value": 1.0, "max_value": 10.5}
    assert params.source == "AVG(amount)"


@pytest.mark.parametrize(
    "config",
    [
        {"column": "amount", "min_value": 0},  # no aggregate
        _cfg(aggregate="avg", min_value=0),  # not in the vocabulary
        _cfg(column="a;b", min_value=0),  # not an identifier
        _cfg(),  # no bound at all — could never fail
        _cfg(min_value="10"),  # a string bound
        _cfg(min_value=True),  # bool is an int subclass
        _cfg(min_value=float("nan")),
        _cfg(min_value=float("inf")),
        _cfg(min_value=0, max=5),  # a typo'd key must not be silently ignored
    ],
)
def test_malformed_config_is_refused(config: dict[str, Any]) -> None:
    with pytest.raises(MonitorConfigError):
        aggregate_params(config)


@pytest.mark.parametrize(
    ("config", "fragment"),
    [
        (_cfg(critical_min=5, min_value=1), "critical_min"),
        (_cfg(min_value=5, warn_min=1), "min_value"),
        (_cfg(max_value=5, critical_max=1), "critical_max"),
        (_cfg(warn_max=9, max_value=5), "max_value"),
        (_cfg(min_value=10, max_value=5), "lower bound 10.0 is above upper bound 5.0"),
        (_cfg(warn_min=10, critical_max=5), "lower bound 10.0 is above upper bound 5.0"),
    ],
)
def test_bands_must_nest(config: dict[str, Any], fragment: str) -> None:
    with pytest.raises(MonitorConfigError, match=fragment):
        aggregate_params(config)


def test_a_single_exact_value_band_is_allowed() -> None:
    assert aggregate_params(_cfg(min_value=3, max_value=3)).bounds == {
        "min_value": 3.0,
        "max_value": 3.0,
    }


# ───────────────────────────── banding ─────────────────────────────

_BANDS = _cfg(
    critical_min=0, min_value=10, warn_min=20, warn_max=80, max_value=90, critical_max=100
)


@pytest.mark.parametrize(
    ("value", "status", "breached"),
    [
        (50, "pass", None),
        (20, "pass", None),  # bounds are inclusive
        (80, "pass", None),
        (15, "warn", "warn_min"),
        (85, "warn", "warn_max"),
        (5, "fail", "min_value"),
        (95, "fail", "max_value"),
        (-1, "critical", "critical_min"),
        (101, "critical", "critical_max"),
    ],
)
def test_two_sided_bands_decide_the_status(value: float, status: str, breached: str | None) -> None:
    outcome = monitor_outcome(AGGREGATE, scalar=value, config=_BANDS, now=_NOW)
    assert outcome.metric_value == float(value)
    assert outcome.observed_value is not None
    assert outcome.observed_value["observed_value"] == float(value)
    assert outcome.observed_value.get("breached_bound") == breached
    # The check's own tier wins; the one-sided threshold columns take no part.
    assert resolve_status(
        outcome, warn_threshold=None, fail_threshold=None, critical_threshold=None
    ) == (status, Decimal(str(float(value))))
    assert outcome.success is (status == "pass")


def test_only_a_lower_bound_never_fails_high() -> None:
    outcome = monitor_outcome(AGGREGATE, scalar=10**12, config=_cfg(min_value=0), now=_NOW)
    assert outcome.severity == "pass"


def test_expected_value_reports_the_authored_bands() -> None:
    outcome = monitor_outcome(AGGREGATE, scalar=1, config=_cfg(min_value=0, warn_max=5), now=_NOW)
    assert outcome.expected_value == {
        "monitor": AGGREGATE,
        "aggregate": "mean",
        "column": "amount",
        "min_value": 0.0,
        "warn_max": 5.0,
    }


# ─────────────────────── driver-boundary scalar ───────────────────────


@pytest.mark.parametrize("scalar", [Decimal("12.50"), 12.5, 12, Decimal("1E+1")])
def test_every_numeric_driver_spelling_is_accepted(scalar: Any) -> None:
    outcome = monitor_outcome(AGGREGATE, scalar=scalar, config=_cfg(min_value=0), now=_NOW)
    assert outcome.metric_value == float(scalar)


def test_null_is_an_error_never_a_zero() -> None:
    """An empty table / all-NULL column has no aggregate: the result must say so, not pass on a
    fabricated 0 (the unknown-as-zero class)."""
    outcome = monitor_outcome(AGGREGATE, scalar=None, config=_cfg(min_value=0), now=_NOW)
    assert outcome.errored
    assert outcome.metric_value is None
    assert outcome.error_message is not None
    assert "AVG(amount) is NULL" in outcome.error_message
    assert resolve_status(
        outcome, warn_threshold=None, fail_threshold=None, critical_threshold=None
    ) == ("error", None)


def test_a_null_stdev_names_the_two_value_minimum() -> None:
    outcome = monitor_outcome(
        AGGREGATE, scalar=None, config=_cfg(aggregate="stdev", min_value=0), now=_NOW
    )
    assert outcome.error_message is not None
    assert "at least two" in outcome.error_message


@pytest.mark.parametrize("scalar", ["12.5", datetime(2026, 1, 1), True])
def test_a_non_numeric_scalar_is_refused_without_echoing_it(scalar: Any) -> None:
    with pytest.raises(MonitorConfigError, match="is not numeric") as exc:
        monitor_outcome(AGGREGATE, scalar=scalar, config=_cfg(min_value=0), now=_NOW)
    assert str(scalar) not in str(exc.value)


@pytest.mark.parametrize("scalar", [float("nan"), float("inf"), Decimal("NaN")])
def test_a_non_finite_scalar_is_refused(scalar: Any) -> None:
    with pytest.raises(MonitorConfigError, match="finite"):
        monitor_outcome(AGGREGATE, scalar=scalar, config=_cfg(min_value=0), now=_NOW)


# ──────────────────────────── per-dialect SQL ────────────────────────────


@pytest.mark.parametrize(
    ("aggregate", "dialect", "expected"),
    [
        # mean/median/stdev are computed over a double: engines keep a DECIMAL AVG at a fixed
        # scale (live-found on Trino and MariaDB), T-SQL truncates an INT one.
        ("mean", "postgresql", 'SELECT AVG(CAST("Amount" AS DOUBLE PRECISION))'),
        ("mean", "mssql", "SELECT AVG(CAST([Amount] AS DOUBLE PRECISION))"),
        ("mean", "trino", 'SELECT AVG(CAST("Amount" AS DOUBLE))'),
        # SQLAlchemy's `Double` would be a 32-bit REAL on Athena and dropped on MySQL.
        ("mean", "awsathena", 'SELECT AVG(CAST("Amount" AS DOUBLE))'),
        ("mean", "mysql", "SELECT AVG((`Amount` + 0E0))"),
        ("stdev", "mysql", "SELECT STDDEV_SAMP((`Amount` + 0E0))"),
        ("stdev", "snowflake", 'SELECT STDDEV_SAMP(CAST("Amount" AS DOUBLE))'),
        ("stdev", "mssql", "SELECT STDEV(CAST([Amount] AS DOUBLE PRECISION))"),
        ("sum", "mssql", "SELECT SUM(CAST([Amount] AS DOUBLE PRECISION))"),
        # A DECIMAL sum is exact at the column's scale, so it stays native.
        ("sum", "databricks", "SELECT SUM(`Amount`)"),
        ("sum", "redshift", 'SELECT SUM("Amount")'),
        (
            "median",
            "postgresql",
            'percentile_cont(0.5) WITHIN GROUP (ORDER BY CAST("Amount" AS DOUBLE PRECISION))',
        ),
        ("median", "snowflake", 'SELECT MEDIAN(CAST("Amount" AS DOUBLE))'),
        ("median", "databricks", "SELECT MEDIAN(CAST(`Amount` AS DOUBLE))"),
        ("median", "redshift", 'SELECT MEDIAN(CAST("Amount" AS DOUBLE PRECISION))'),
        (
            "median",
            "mssql",
            "PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY CAST([Amount] AS DOUBLE PRECISION))"
            " OVER ()",
        ),
        ("min", "mysql", "SELECT min(`Amount`)"),
        ("max", "trino", 'SELECT max("Amount")'),
    ],
)
def test_each_dialect_spells_the_aggregate_itself(
    aggregate: str, dialect: str, expected: str
) -> None:
    assert expected in _sql(aggregate, _dialects()[dialect], column="Amount")


def test_the_windowed_tsql_median_keeps_one_row() -> None:
    assert _sql("median", _dialects()["mssql"]).startswith("SELECT TOP ")


def test_a_lower_case_column_stays_bare_so_it_folds() -> None:
    assert "min(amount) AS min_1 FROM sales.orders" in _sql("min", _dialects()["snowflake"])


@pytest.mark.parametrize("dialect", aggregate_sql.NO_EXACT_MEDIAN_DIALECTS)
def test_a_dialect_without_an_exact_median_refuses_to_compile(dialect: str) -> None:
    from sqlalchemy.exc import CompileError

    if dialect == "mariadb":
        pytest.skip("no separate MariaDB dialect object is built here")
    with pytest.raises(CompileError, match="no exact median"):
        _sql("median", _dialects()[dialect])


def test_the_author_gate_and_the_compiler_agree_on_median() -> None:
    """`SqlEngineSpec.exact_median` refuses at author time what the compiler refuses at run
    time — the two lists must name the same engines."""
    dialects = _dialects()
    refused = {
        conn
        for conn, spec in SQL_ENGINES.items()
        if spec.drivername.split("+")[0] in {"mysql", "trino", "awsathena"}
    }
    assert refused == {c for c, s in SQL_ENGINES.items() if not s.exact_median}
    for conn in SQL_ENGINES:
        name = SQL_ENGINES[conn].drivername.split("+")[0]
        if name in dialects:
            compiles = name not in aggregate_sql.NO_EXACT_MEDIAN_DIALECTS
            assert compiles is SQL_ENGINES[conn].exact_median, conn


@pytest.mark.parametrize("aggregate", AGGREGATES)
def test_every_aggregate_compiles_on_every_dialect_that_offers_it(aggregate: str) -> None:
    for name, dialect in _dialects().items():
        if aggregate == "median" and name in aggregate_sql.NO_EXACT_MEDIAN_DIALECTS:
            continue
        assert "FROM sales.orders" in _sql(aggregate, dialect).replace("`", "")


# ───────────────────────────── frame path ─────────────────────────────


@pytest.mark.parametrize(
    ("aggregate", "expected"),
    [
        ("mean", 20.0),
        ("median", 20.0),
        ("sum", 60.0),
        ("stdev", 10.0),
        ("min", 10.0),
        ("max", 30.0),
    ],
)
def test_frame_aggregate_skips_nulls_like_sql(aggregate: str, expected: float) -> None:
    series = pd.Series([10, None, 20, 30], dtype="float64")
    assert aggregate_of_series(series, aggregate, source="X") == pytest.approx(expected)


@pytest.mark.parametrize("aggregate", AGGREGATES)
def test_frame_aggregate_of_no_values_is_none_not_zero(aggregate: str) -> None:
    for series in (pd.Series([], dtype="float64"), pd.Series([None, None], dtype="float64")):
        assert aggregate_of_series(series, aggregate, source="X") is None


def test_frame_stdev_of_one_value_is_none_like_stddev_samp() -> None:
    assert aggregate_of_series(pd.Series([5.0]), "stdev", source="X") is None


def test_frame_aggregate_reads_an_arrow_backed_decimal_column() -> None:
    """Iceberg hands back Arrow-backed pandas: a decimal column is `ArrowDtype(decimal128)`, and a
    numpy frame fixture would never exercise it."""
    arrow = pa.table(
        {"amount": pa.array([Decimal("1.50"), None, Decimal("2.50")], pa.decimal128(9, 2))}
    )
    series = arrow.to_pandas(types_mapper=pd.ArrowDtype)["amount"]
    assert aggregate_of_series(series, "sum", source="X") == pytest.approx(4.0)


def test_frame_aggregate_reads_an_object_column_of_decimals() -> None:
    series = pd.Series([Decimal("1.5"), None, Decimal("2.5")], dtype="object")
    assert aggregate_of_series(series, "mean", source="X") == pytest.approx(2.0)


@pytest.mark.parametrize(
    "series",
    [
        pd.Series(["a", "b"]),
        pd.Series([True, False]),
        pd.Series(pd.to_datetime(["2026-01-01"])),
    ],
)
def test_frame_aggregate_refuses_a_non_numeric_column(series: pd.Series) -> None:
    with pytest.raises(MonitorConfigError):
        aggregate_of_series(series, "max", source="X")
