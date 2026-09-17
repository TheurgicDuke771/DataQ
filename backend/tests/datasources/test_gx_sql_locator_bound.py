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


def test_frame_batch_keeps_the_complete_lane() -> None:
    """The pandas lanes are untouched: they hold the batch in memory already, and `COMPLETE` is
    the only format that returns their full `unexpected_index_list` for #1196 to cap.
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
