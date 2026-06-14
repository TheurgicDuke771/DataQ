"""Custom-SQL round-trip through the real GX path (ADR 0019).

Locks the de-risked contract: a custom-SQL check runs as a GX
``UnexpectedRowsExpectation`` against a SQL batch — ``{batch}`` substitution works
and ``gx_runner`` maps **0 rows → success**, **≥1 row → failure**, with the
unexpected row count surfaced as ``observed_value``. This guards against a GX
upgrade silently changing that shape (CLAUDE.md §5 — GX's API drifts).

``UnexpectedRowsExpectation`` is SQL-only, so (unlike the in-memory DataFrame
runner tests) this needs a live SQL backend; the Postgres behind
``TEST_DATABASE_URL`` stands in for the warehouse (which has no live connect in
CI). Self-contained: it creates and drops its own probe table. Skips without
``TEST_DATABASE_URL``.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import great_expectations as gx
import pytest
from sqlalchemy import create_engine, text

from backend.app.datasources.base import CheckSpec
from backend.app.datasources.gx_runner import run_expectations
from backend.app.services.custom_sql import CUSTOM_SQL_EXPECTATION_TYPE

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="requires TEST_DATABASE_URL")


@pytest.fixture
def sql_batch() -> Iterator[tuple[object, object]]:
    """An ephemeral GX context + a whole-table batch over a 3-row probe table."""
    url = TEST_DATABASE_URL
    assert url is not None  # narrowed by the module-level skipif
    engine = create_engine(url)
    table = f"custom_sql_probe_{uuid.uuid4().hex[:8]}"
    with engine.begin() as conn:
        conn.execute(text(f"CREATE TABLE {table} (n integer)"))
        conn.execute(text(f"INSERT INTO {table} VALUES (1), (2), (3)"))
    try:
        ctx = gx.get_context(mode="ephemeral")
        ds = ctx.data_sources.add_postgres(f"pg_{uuid.uuid4().hex[:8]}", connection_string=url)
        asset = ds.add_table_asset("probe", table_name=table)
        yield ctx, asset.add_batch_definition_whole_table("bd")
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP TABLE {table}"))
        engine.dispose()


def _run(sql_batch: tuple[object, object], query: str) -> object:
    ctx, bd = sql_batch
    outcome = run_expectations(
        ctx,
        batch_definition=bd,
        checks=[
            CheckSpec(
                expectation_type=CUSTOM_SQL_EXPECTATION_TYPE,
                kwargs={"unexpected_rows_query": query},
            )
        ],
        name=f"s_{uuid.uuid4().hex[:8]}",
    )
    return outcome


def test_zero_unexpected_rows_passes(sql_batch: tuple[object, object]) -> None:
    outcome = _run(sql_batch, "SELECT * FROM {batch} WHERE n > 100")
    assert outcome.success is True  # type: ignore[attr-defined]
    assert outcome.checks[0].success is True  # type: ignore[attr-defined]


def test_unexpected_rows_fail_with_count(sql_batch: tuple[object, object]) -> None:
    outcome = _run(sql_batch, "SELECT * FROM {batch} WHERE n > 0")
    check = outcome.checks[0]  # type: ignore[attr-defined]
    assert outcome.success is False  # type: ignore[attr-defined]
    assert check.success is False
    # The unexpected row count is surfaced as observed_value (the scalar a later
    # count-banding severity enhancement would read — ADR 0019).
    assert check.observed_value == {"observed_value": 3}
