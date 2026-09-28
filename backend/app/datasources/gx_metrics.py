"""DataQ's overrides of GX metric providers — imported once, before any validation runs.

GX's `unexpected_rows_query.table` metric (the custom-SQL row sample) sends the user's
statement unbounded and `fetchmany`s at most `MAX_RESULT_RECORDS` rows client-side, so the
warehouse materialises every failing row first (#2082). The override bounds the statement at
the same cap, so every result format sees the same number of rows as before. A statement that
orders its rows is left unbounded: an ORDER BY inside a derived table is not guaranteed to
survive the outer LIMIT, so wrapping it could return an arbitrary 200 rather than the first.
"""

from __future__ import annotations

import re
from typing import Any

import sqlalchemy as sa
from great_expectations.constants import MAX_RESULT_RECORDS
from great_expectations.core.metric_domain_types import MetricDomainTypes
from great_expectations.execution_engine import SqlAlchemyExecutionEngine
from great_expectations.expectations.metrics.metric_provider import metric_value
from great_expectations.expectations.metrics.query_metrics.query_table.unexpected_rows_query_table import (  # noqa: E501
    UnexpectedRowsQueryTable,
)

#: T-SQL rejects an ORDER BY inside a derived table without TOP, which a user's statement
#: may carry; it keeps GX's client-side bound only.
_UNWRAPPED_DIALECTS = frozenset({"mssql"})
#: Anywhere in the statement, deliberately: a nested ORDER BY only costs the old behaviour.
_ORDER_BY = re.compile(r"\border\s+by\b", re.IGNORECASE)


def bounded_statement(statement: str, *, dialect: Any, limit: int) -> Any:
    """``statement`` as a derived table with a dialect-compiled row limit."""
    if dialect.name in _UNWRAPPED_DIALECTS or _ORDER_BY.search(statement):
        return sa.text(statement)
    derived = sa.text(f"({statement}) AS dataq_unexpected_rows")
    return sa.select(sa.literal_column("*")).select_from(derived).limit(limit)


class BoundedUnexpectedRowsQueryTable(UnexpectedRowsQueryTable):  # type: ignore[misc]
    """`unexpected_rows_query.table` with the row cap pushed into the statement.

    GX registers providers from the `metric_value` methods a class declares, so the SQL
    provider is redeclared here — the body is `QueryTable._sqlalchemy` with the bound added.
    """

    @metric_value(engine=SqlAlchemyExecutionEngine)  # type: ignore[untyped-decorator]
    def _sqlalchemy(
        cls,  # noqa: N805 — GX's `metric_value` makes this a classmethod
        execution_engine: SqlAlchemyExecutionEngine,
        metric_domain_kwargs: dict[str, Any],
        metric_value_kwargs: dict[str, Any],
        metrics: dict[str, Any],
        runtime_configuration: dict[str, Any],
    ) -> list[dict[str, Any]]:
        batch_selectable, _, _ = execution_engine.get_compute_domain(
            metric_domain_kwargs, domain_type=MetricDomainTypes.TABLE
        )
        substituted = cls._get_substituted_batch_subquery_from_query_and_batch_selectable(
            query=cls._get_query_from_metric_value_kwargs(metric_value_kwargs),
            batch_selectable=batch_selectable,
            execution_engine=execution_engine,
        )
        if metric_value_kwargs.get("fetch_all", False):
            records: list[dict[str, Any]] = (
                cls._get_sqlalchemy_records_from_substituted_batch_subquery(
                    substituted_batch_subquery=substituted,
                    execution_engine=execution_engine,
                    fetch_all=True,
                )
            )
            return records
        statement = bounded_statement(
            substituted, dialect=execution_engine.engine.dialect, limit=MAX_RESULT_RECORDS
        )
        rows = execution_engine.execute_query(statement).fetchmany(MAX_RESULT_RECORDS)
        return [row._asdict() for row in rows]
