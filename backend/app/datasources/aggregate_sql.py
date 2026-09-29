"""Per-dialect SQL for the `aggregate` monitor kind (#1602).

Each aggregate is a Core function element compiled by the connection's own dialect, so the
column is quoted by that dialect and no SQL string is hand-assembled. Only the spellings that
differ between engines are overridden.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Float, func, select
from sqlalchemy.exc import CompileError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql import ColumnElement, Select, TableClause
from sqlalchemy.sql.functions import FunctionElement

from backend.app.datasources.monitors import (
    AGGREGATE_MAX,
    AGGREGATE_MEAN,
    AGGREGATE_MEDIAN,
    AGGREGATE_MIN,
    AGGREGATE_STDEV,
    AGGREGATE_SUM,
)

# Dialects with no exact median aggregate: Trino and Athena offer only `approx_percentile`, MySQL
# has none and MariaDB only a window form MySQL lacks. Refused at author time too.
NO_EXACT_MEDIAN_DIALECTS = ("mysql", "mariadb", "trino", "awsathena")

_DOUBLE_TYPE = {
    "snowflake": "DOUBLE",
    "databricks": "DOUBLE",
    "trino": "DOUBLE",
    "awsathena": "DOUBLE",
}


class _Avg(FunctionElement[Any]):
    type = Float()
    inherit_cache = True


class _Sum(FunctionElement[Any]):
    type = Float()
    inherit_cache = True


class _Median(FunctionElement[Any]):
    type = Float()
    inherit_cache = True


class _StdevSamp(FunctionElement[Any]):
    type = Float()
    inherit_cache = True


def _arg(compiler: Any, element: FunctionElement[Any], **kw: Any) -> str:
    return str(compiler.process(next(iter(element.clauses)), **kw))


def _double(compiler: Any, element: FunctionElement[Any], **kw: Any) -> str:
    """The argument cast to a double-precision float. Engines keep a DECIMAL aggregate at a
    fixed scale — Trino, Athena and Redshift return AVG at the column's own scale, MySQL and
    MariaDB at scale + 4 (MariaDB's STDDEV_SAMP too), Databricks at scale + 4 — and T-SQL
    truncates AVG of an INT and overflows SUM at 2^31; every one of those is a rounded answer.

    The type is spelled per dialect, not taken from SQLAlchemy's `Double`: the MySQL dialect
    drops that CAST altogether and the Athena one emits a 32-bit REAL.
    """
    arg = _arg(compiler, element, **kw)
    dialect = compiler.dialect.name
    if dialect in ("mysql", "mariadb"):
        # An approximate literal makes the expression DOUBLE on every MySQL 8 / MariaDB version;
        # `CAST(... AS DOUBLE)` needs MySQL 8.0.17.
        return f"({arg} + 0E0)"
    return f"CAST({arg} AS {_DOUBLE_TYPE.get(dialect, 'DOUBLE PRECISION')})"


@compiles(_Avg)
def _avg(element: _Avg, compiler: Any, **kw: Any) -> str:
    return f"AVG({_double(compiler, element, **kw)})"


@compiles(_Sum)
def _sum(element: _Sum, compiler: Any, **kw: Any) -> str:
    return f"SUM({_arg(compiler, element, **kw)})"


# A DECIMAL sum is exact at the column's scale everywhere; only T-SQL's INT sum overflows.
@compiles(_Sum, "mssql")
def _sum_mssql(element: _Sum, compiler: Any, **kw: Any) -> str:
    return f"SUM({_double(compiler, element, **kw)})"


@compiles(_Median)
def _median_ordered_set(element: _Median, compiler: Any, **kw: Any) -> str:
    return f"percentile_cont(0.5) WITHIN GROUP (ORDER BY {_double(compiler, element, **kw)})"


@compiles(_Median, "snowflake")
@compiles(_Median, "databricks")
@compiles(_Median, "redshift")
def _median_native(element: _Median, compiler: Any, **kw: Any) -> str:
    return f"MEDIAN({_double(compiler, element, **kw)})"


@compiles(_Median, "mssql")
def _median_mssql(element: _Median, compiler: Any, **kw: Any) -> str:
    # T-SQL's PERCENTILE_CONT is window-only; `aggregate_statement` keeps one of its equal rows.
    return (
        f"PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY {_double(compiler, element, **kw)}) OVER ()"
    )


def _median_unsupported(element: _Median, compiler: Any, **kw: Any) -> str:
    raise CompileError(f"no exact median aggregate on the {compiler.dialect.name} dialect")


for _dialect in NO_EXACT_MEDIAN_DIALECTS:
    compiles(_Median, _dialect)(_median_unsupported)


@compiles(_StdevSamp)
def _stdev(element: _StdevSamp, compiler: Any, **kw: Any) -> str:
    return f"STDDEV_SAMP({_double(compiler, element, **kw)})"


@compiles(_StdevSamp, "mssql")
def _stdev_mssql(element: _StdevSamp, compiler: Any, **kw: Any) -> str:
    return f"STDEV({_double(compiler, element, **kw)})"


_EXPRESSIONS: dict[str, Any] = {
    AGGREGATE_MEAN: _Avg,
    AGGREGATE_SUM: _Sum,
    AGGREGATE_MEDIAN: _Median,
    AGGREGATE_STDEV: _StdevSamp,
    AGGREGATE_MIN: func.min,
    AGGREGATE_MAX: func.max,
}


def aggregate_statement(
    target: TableClause, aggregate: str, column: ColumnElement[Any]
) -> Select[Any]:
    """``SELECT <aggregate>(column) FROM target`` for the executing dialect to compile."""
    statement = select(_EXPRESSIONS[aggregate](column)).select_from(target)
    # The windowed T-SQL median yields one equal row per input row; harmless elsewhere.
    return statement.limit(1) if aggregate == AGGREGATE_MEDIAN else statement
