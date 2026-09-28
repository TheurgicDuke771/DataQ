"""The failing-row fetch on a SQL batch is bounded IN SQL, not after the fact (#1534).

Real GX end to end on a sqlite SQLAlchemy batch — the Snowflake / Unity-Catalog-pushdown shape
(metric providers are registered against the engine CLASS, so the dialect is irrelevant to which
code path runs). The assertions sit on the seam — the statements GX emits — because the returned
sample has been capped at capture since #1196 and its length proves nothing about how many rows
the warehouse produced.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import great_expectations as gx
import pandas as pd
import pytest
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.engine import Engine

from backend.app.datasources.base import SAMPLE_ROW_CAP, CheckOutcome, CheckSpec
from backend.app.datasources.gx_runner import _execute, _is_sql_batch, run_expectations
from backend.app.services.custom_sql import CUSTOM_SQL_EXPECTATION_TYPE

_FAILING_ROWS = 500
_NOT_NULL = "expect_column_values_to_not_be_null"
_INDEX_COLUMNS = ["customer_id"]

_CHECKS = [
    CheckSpec(_NOT_NULL, {"column": "order_number"}),
    CheckSpec("expect_column_values_to_be_between", {"column": "qty", "min_value": 0}),
    CheckSpec("expect_column_max_to_be_between", {"column": "qty", "min_value": 0, "max_value": 3}),
]


def _frame(failing: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_number": [None] * failing + ["OK"] * 7,
            "customer_id": list(range(1000, 1000 + failing + 7)),
            "qty": [-1] * failing + [1] * 7,
        }
    )


def _sql_batch_definition(tmp_path: Path, *, failing: int, name: str) -> tuple[Any, Any]:
    db = tmp_path / f"{name}.db"
    url = f"sqlite:///{db}"
    engine = sa.create_engine(url)
    _frame(failing).to_sql("orders", engine, index=False)
    engine.dispose()
    context = gx.get_context(mode="ephemeral")
    source = context.data_sources.add_sqlite(name=f"sq-{name}", connection_string=url)
    asset = source.add_table_asset(name="orders", table_name="orders")
    return context, asset.add_batch_definition_whole_table("bd")


class _StatementSpy:
    """Every statement GX's execution engine sends to the driver, with its bound params."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def __enter__(self) -> _StatementSpy:
        event.listen(Engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *exc: object) -> None:
        event.remove(Engine, "before_cursor_execute", self._record)

    def _record(
        self,
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        self.calls.append((" ".join(statement.split()), parameters))

    def failing_row_queries(self) -> list[tuple[str, Any]]:
        """The row-returning queries over the failing rows — not the aggregate counts."""
        return [
            (statement, params)
            for statement, params in self.calls
            if "order_number IS NULL" in statement and "sum(" not in statement
        ]


def _outcomes(outcome: Any) -> dict[str, CheckOutcome]:
    return {check.expectation_type: check for check in outcome.checks}


def test_sql_batch_limits_every_failing_row_query(tmp_path: Path) -> None:
    """Both row-returning queries — the locator query AND the unexpected-VALUES query — carry a
    SQL LIMIT of `SAMPLE_ROW_CAP`. Under COMPLETE the values query had none, so the warehouse
    materialised every failing row and the driver streamed them into the worker (#1534).
    """
    context, batch_definition = _sql_batch_definition(tmp_path, failing=_FAILING_ROWS, name="wide")
    with _StatementSpy() as spy:
        outcome = run_expectations(
            context,
            batch_definition=batch_definition,
            checks=_CHECKS,
            name="bounded",
            index_columns=_INDEX_COLUMNS,
        )

    queries = spy.failing_row_queries()
    # the locator query and the unexpected-values query
    assert len(queries) == 2
    for statement, params in queries:
        assert "LIMIT" in statement, statement
        assert SAMPLE_ROW_CAP in tuple(params), (statement, params)

    # the bound trims what is FETCHED, never the reported totals
    sample = _outcomes(outcome)[_NOT_NULL].sample_failures
    assert sample is not None
    assert sample["unexpected_count"] == _FAILING_ROWS
    assert len(sample["unexpected_index_list"]) == SAMPLE_ROW_CAP
    assert len(sample["partial_unexpected_list"]) == SAMPLE_ROW_CAP


def test_sql_batch_without_index_columns_is_bounded_too(tmp_path: Path) -> None:
    """The no-locator lane (no identifier column configured) emits the same unbounded values
    query under COMPLETE, so it is bounded on the same seam.
    """
    context, batch_definition = _sql_batch_definition(tmp_path, failing=_FAILING_ROWS, name="noidx")
    with _StatementSpy() as spy:
        run_expectations(
            context, batch_definition=batch_definition, checks=_CHECKS, name="bounded-noidx"
        )

    queries = spy.failing_row_queries()
    assert len(queries) == 1
    statement, params = queries[0]
    assert "LIMIT" in statement, statement
    assert SAMPLE_ROW_CAP in tuple(params), (statement, params)


@pytest.mark.parametrize("failing", [3, SAMPLE_ROW_CAP, 200])
def test_sql_sample_output_is_unchanged(tmp_path: Path, failing: int) -> None:
    """Byte-compatibility: the mapped outcome — sample keys, identifier locators (#415), the
    counts the severity bands and the redactor read — is identical to what the unbounded
    COMPLETE run produced, at, below and above the sample cap.
    """
    context, batch_definition = _sql_batch_definition(tmp_path, failing=failing, name="new")
    bounded = run_expectations(
        context,
        batch_definition=batch_definition,
        checks=_CHECKS,
        name="new",
        index_columns=_INDEX_COLUMNS,
    )
    legacy_context, legacy_batch = _sql_batch_definition(tmp_path, failing=failing, name="legacy")
    legacy = _execute(
        legacy_context,
        batch_definition=legacy_batch,
        checks=_CHECKS,
        name="legacy",
        batch_parameters=None,
        result_format={
            "result_format": "COMPLETE",
            "unexpected_index_column_names": _INDEX_COLUMNS,
        },
    )

    assert bounded == legacy
    sample = _outcomes(bounded)[_NOT_NULL].sample_failures
    assert sample is not None
    rows = sample["unexpected_index_list"]
    assert all(set(row) == {"customer_id", "order_number"} for row in rows)
    assert [row["customer_id"] for row in rows] == list(range(1000, 1000 + min(failing, 20)))


def test_custom_sql_still_reports_its_unexpected_row_count(tmp_path: Path) -> None:
    """The custom-SQL lane is the only one whose GX payload actually changes: under COMPLETE
    `UnexpectedRowsExpectation` also returns `details.unexpected_rows`, which nothing reads.
    What IS read — the unexpected row count as `observed_value` — must survive.
    """
    context, batch_definition = _sql_batch_definition(tmp_path, failing=_FAILING_ROWS, name="csql")
    outcome = run_expectations(
        context,
        batch_definition=batch_definition,
        checks=[
            CheckSpec(
                CUSTOM_SQL_EXPECTATION_TYPE,
                {"unexpected_rows_query": "SELECT * FROM {batch} WHERE order_number IS NULL"},
            )
        ],
        name="custom-sql",
    )
    check = outcome.checks[0]
    assert check.errored is False
    assert check.success is False
    assert check.observed_value == {"observed_value": _FAILING_ROWS}


def test_custom_sql_row_fetch_is_bounded_in_sql(tmp_path: Path) -> None:
    """GX sends the custom-SQL row sample (`unexpected_rows_query.table`) unbounded and
    `fetchmany`s 200 rows client-side, so the warehouse materialises every failing row (#2082).
    The row statement must carry the LIMIT; the COUNT must not, or the count is capped too.
    """
    from great_expectations.constants import MAX_RESULT_RECORDS

    query = "SELECT * FROM {batch} WHERE order_number IS NULL"
    context, batch_definition = _sql_batch_definition(tmp_path, failing=_FAILING_ROWS, name="rows")
    with _StatementSpy() as spy:
        outcome = run_expectations(
            context,
            batch_definition=batch_definition,
            checks=[CheckSpec(CUSTOM_SQL_EXPECTATION_TYPE, {"unexpected_rows_query": query})],
            name="custom-sql-bound",
        )

    user_statements = [(sql, p) for sql, p in spy.calls if "order_number IS NULL" in sql]
    counts = [sql for sql, _ in user_statements if "COUNT(" in sql.upper()]
    rows = [(sql, p) for sql, p in user_statements if "COUNT(" not in sql.upper()]
    assert len(counts) == 1 and "LIMIT" not in counts[0].upper(), counts
    assert len(rows) == 1, rows
    statement, params = rows[0]
    assert "LIMIT" in statement.upper(), statement
    assert MAX_RESULT_RECORDS in tuple(params or ()), (statement, params)
    assert outcome.checks[0].observed_value == {"observed_value": _FAILING_ROWS}


def test_bounded_statement_wraps_only_where_the_limit_cannot_reorder() -> None:
    """T-SQL rejects an ORDER BY inside a derived table (#2138), and elsewhere a derived
    table's ORDER BY need not survive the outer LIMIT — both keep GX's client-side cut."""
    from sqlalchemy.dialects import mssql, postgresql

    from backend.app.datasources.gx_metrics import bounded_statement

    t_sql: Any = mssql.dialect()  # type: ignore[no-untyped-call]
    postgres: Any = postgresql.dialect()  # type: ignore[no-untyped-call]
    plain = "SELECT * FROM t WHERE a < 0"
    ordered = "SELECT * FROM t WHERE a < 0 Order  By a"

    assert str(bounded_statement(plain, dialect=t_sql, limit=200)) == plain
    assert str(bounded_statement(ordered, dialect=postgres, limit=200)) == ordered
    compiled = str(
        bounded_statement(plain, dialect=postgres, limit=200).compile(
            dialect=postgres, compile_kwargs={"literal_binds": True}
        )
    )
    assert compiled.endswith("LIMIT 200") and f"({plain})" in compiled


def test_undetermined_lane_falls_back_loudly() -> None:
    """A GX rename of the `data_asset` / `datasource` chain drops the SQL lanes onto the frame
    lane's wider cap — a larger locator fetch out of the warehouse, invisible unless it says so.
    """
    from structlog.testing import capture_logs

    with capture_logs() as logs:
        assert _is_sql_batch(object()) is False
    assert any(entry["event"] == "gx_batch_lane_undetermined" for entry in logs)


def test_frame_batch_is_not_the_sql_lane() -> None:
    """A pandas batch takes the frame cap, not the SQL one — and still reports the true total
    with a sample bounded at capture (#1196). The frame lane's own bound is #1995.
    """
    context = gx.get_context(mode="ephemeral")
    asset = context.data_sources.add_pandas(name="p").add_dataframe_asset(name="t")
    batch_definition = asset.add_batch_definition_whole_dataframe(name="wd")
    assert _is_sql_batch(batch_definition) is False

    outcome = run_expectations(
        context,
        batch_definition=batch_definition,
        checks=[CheckSpec(_NOT_NULL, {"column": "order_number"})],
        name="frame",
        batch_parameters={"dataframe": _frame(_FAILING_ROWS)},
        index_columns=_INDEX_COLUMNS,
    )
    sample = _outcomes(outcome)[_NOT_NULL].sample_failures
    assert sample is not None
    assert sample["unexpected_count"] == _FAILING_ROWS
    assert len(sample["unexpected_index_list"]) == SAMPLE_ROW_CAP
