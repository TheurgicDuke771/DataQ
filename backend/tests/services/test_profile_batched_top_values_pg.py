"""The batched top-values query, executed for real (#327).

`test_profile_service.py` compiles the builders to SQL and inspects the text —
useful, and not evidence that any engine will *run* the statement. The batched
form leans on three things a compile-time assertion cannot check: an ``ORDER BY
… LIMIT`` inside a derived table, a window function computed over an aggregate,
and a chain of LEFT JOINs whose padding NULLs must not read as data. So this
module runs the generated SQL against the Postgres behind ``TEST_DATABASE_URL``,
which stands in for the warehouse exactly as the custom-SQL GX test does (a live
Snowflake / Unity Catalog connect is not available in CI).

It is also what caught the first design. That one was the obvious ``UNION ALL``
of per-column selects with ``NULL``-padded value slots, it compiled cleanly, and
Postgres rejected it outright — a union resolves an untyped ``NULL`` pairwise and
left to right, so two padding slots settled on ``text`` before the branch owning
the slot was reached (``UNION types text and timestamp … cannot be matched``). No
amount of SQL-text assertion would have found that.

The assertion is **equivalence, not plausibility**: for every column, the
batched result must equal what the per-column query returns — same values, same
Python types, same order, same tie-break, same treatment of an all-null column.
That is the only shape that can catch the failure mode this change risks, which
is a profile that still *looks* right while a type or an ordering quietly moved.

Postgres is one dialect. Per the #953 rule, cross-dialect behaviour is only ever
proven by a live run, and the per-column fallback in `_fetch_top_values` exists
for exactly the dialect that disagrees.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import create_engine, text

from backend.app.services import profile_service
from backend.app.services.profile_service import (
    build_batched_top_values_query,
    collect_batched_top_values,
    profile_table,
)
from backend.tests.support.fake_secret_store import FakeSecretStore

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="requires TEST_DATABASE_URL")

# Deliberately adversarial-but-legal column names (the allowlist refuses spaces,
# dots and quotes outright — see `test_validate_identifier_rejects_unsafe`):
#   * `amount`      — plain lower-case, emitted BARE so the warehouse folds it
#   * `Status`      — mixed case, only reachable QUOTED (#476)
#   * `ORDER_TS`    — upper-case timestamp, also quoted
#   * `amount$usd`  — the `$` the identifier allowlist admits, and deliberately
#                     LOWER cardinality than the rest, so the LEFT JOIN really
#                     does pad a short column with NULLs at the last rank
#   * `net`         — NUMERIC, so the value arrives as a Decimal, not a float
#   * `all_null`    — contributes no rows at all
_COLUMNS = ["amount", "Status", "ORDER_TS", "amount$usd", "net", "all_null"]


@pytest.fixture
def probe_table() -> Iterator[tuple[Any, str]]:
    """A live connection plus a probe table seeded with ties, nulls and 6 types."""
    url = TEST_DATABASE_URL
    assert url is not None  # narrowed by the module-level skipif
    engine = create_engine(url)
    name = f"profile_probe_{uuid.uuid4().hex[:8]}"
    with engine.begin() as conn:
        conn.execute(
            text(
                f'CREATE TABLE public."{name}" ('
                "  amount integer,"
                '  "Status" text,'
                '  "ORDER_TS" timestamp,'
                '  "amount$usd" text,'
                "  net numeric(10, 2),"
                "  all_null text"
                ")"
            )
        )
        # `amount` 1 and 2 both appear twice — a COUNT tie, so the `ORDER BY
        # count DESC, <col>` tie-break is actually exercised rather than assumed.
        # Every column carries a NULL so the `IS NOT NULL` filter matters.
        conn.execute(
            text(
                f'INSERT INTO public."{name}" VALUES '
                "(1, 'b', TIMESTAMP '2026-01-01 00:00:00', 'x', 10.50, NULL),"
                "(1, 'b', TIMESTAMP '2026-01-01 00:00:00', 'x', 10.50, NULL),"
                "(2, 'a', TIMESTAMP '2026-02-02 00:00:00', 'y', 20.25, NULL),"
                "(2, 'a', TIMESTAMP '2026-02-02 00:00:00', 'y', 20.25, NULL),"
                "(3, 'c', TIMESTAMP '2026-03-03 00:00:00', 'y', 30.00, NULL),"
                "(NULL, NULL, NULL, NULL, NULL, NULL)"
            )
        )
    try:
        with engine.connect() as conn:
            yield conn, name
    finally:
        with engine.begin() as conn:
            conn.execute(text(f'DROP TABLE public."{name}"'))
        engine.dispose()


def _per_column(conn: Any, table: str, columns: list[str], top_n: int) -> dict[str, list[Any]]:
    """The pre-#327 path, run for real — the reference every assertion compares to."""
    return {
        col: [
            dict(row)
            for row in conn.execute(
                profile_service.build_top_values_query("public", table, col, top_n)
            ).mappings()
        ]
        for col in columns
    }


def _batched(conn: Any, table: str, columns: list[str], top_n: int) -> dict[str, list[Any]]:
    rows = list(
        conn.execute(build_batched_top_values_query("public", table, columns, top_n)).mappings()
    )
    return {
        col: [dict(entry) for entry in entries]
        for col, entries in collect_batched_top_values(rows, columns).items()
    }


def test_batched_matches_per_column_exactly(probe_table: tuple[Any, str]) -> None:
    conn, table = probe_table
    reference = _per_column(conn, table, _COLUMNS, 10)
    batched = _batched(conn, table, _COLUMNS, 10)

    # An all-null column returns no rows on either path — the per-column query
    # yields [], the batch simply contributes no branch rows; `assemble_profile`
    # renders both as [].
    assert reference["all_null"] == []
    assert "all_null" not in batched

    # Columns of DIFFERENT length is the case the join has to get right: the rank
    # driver runs to `top_n`, so a short column is LEFT-JOINed to NULL at the
    # ranks it does not reach, and those must not surface as top values.
    assert len(batched["amount"]) == 3 and len(batched["amount$usd"]) == 2

    for col in _COLUMNS:
        if col == "all_null":
            continue
        assert batched[col] == reference[col], col
        # Equality alone would accept 10.5 for Decimal('10.50'), so pin the types
        # too: a union that coerced its slots would show up right here.
        for got, want in zip(batched[col], reference[col], strict=True):
            assert type(got["value"]) is type(want["value"])


def test_batched_preserves_the_count_tie_break_order(probe_table: tuple[Any, str]) -> None:
    conn, table = probe_table
    # amount: 1→2 rows, 2→2 rows, 3→1 row. The tie between 1 and 2 is broken by
    # the value, ascending — the ONE ordering detail a Python re-sort of an
    # unordered union would be free to get wrong.
    assert [row["value"] for row in _batched(conn, table, ["amount"], 10)["amount"]] == [1, 2, 3]
    assert [row["freq"] for row in _batched(conn, table, ["amount"], 10)["amount"]] == [2, 2, 1]


def test_batched_honours_top_n_per_column_not_across_the_join(
    probe_table: tuple[Any, str],
) -> None:
    # The LIMIT lives in each column's derived table and the rank driver stops at
    # `top_n`, so `top_n=1` means one row PER column — not one row in total.
    batched = _batched(conn=probe_table[0], table=probe_table[1], columns=_COLUMNS, top_n=1)
    assert {col: len(rows) for col, rows in batched.items()} == {
        col: 1 for col in _COLUMNS if col != "all_null"
    }


def test_batched_keeps_numeric_and_timestamp_native(probe_table: tuple[Any, str]) -> None:
    conn, table = probe_table
    batched = _batched(conn, table, ["net", "ORDER_TS"], 10)
    assert isinstance(batched["net"][0]["value"], Decimal)
    assert batched["ORDER_TS"][0]["value"].year == 2026
    # A shared `value` projection would have unified NUMERIC + TIMESTAMP + text
    # into VARCHAR here (Snowflake) or refused the union outright (Postgres);
    # giving each column its own joined output column is what keeps both native.


def test_profile_table_end_to_end_matches_the_per_column_path(
    probe_table: tuple[Any, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole service call over a real engine: batched vs fallback, same result.

    `_fetch_top_values` is the seam that chooses, so forcing it to the fallback
    is how the two paths are compared without stubbing the SQL itself.
    """
    conn, table = probe_table

    @contextmanager
    def fake_open(connection: Any, secret_store: Any) -> Iterator[Any]:
        yield conn

    monkeypatch.setattr(profile_service, "_open_connection", fake_open)
    connection = type(
        "Conn", (), {"type": "snowflake", "config": {"schema": "public"}, "secret_ref": "ref"}
    )()
    kwargs: dict[str, Any] = {
        "table": table,
        "schema": "public",
        "columns": _COLUMNS,
        "top_n": 10,
        "secret_store": FakeSecretStore({"ref": "pw"}),
    }
    batched = profile_table(connection, **kwargs)

    monkeypatch.setattr(
        profile_service,
        "build_batched_top_values_query",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("dialect says no")),
    )
    fallback = profile_table(connection, **kwargs)

    assert batched == fallback
    amount = next(c for c in batched.columns if c.column == "amount")
    assert amount.top_values == [
        {"value": 1, "count": 2},
        {"value": 2, "count": 2},
        {"value": 3, "count": 1},
    ]
    assert next(c for c in batched.columns if c.column == "all_null").top_values == []
