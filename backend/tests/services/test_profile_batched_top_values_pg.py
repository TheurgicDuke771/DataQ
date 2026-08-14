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
batched result must equal what the production per-column path returns — same values, same
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
from sqlalchemy import create_engine, literal_column, select, text

from backend.app.datasources.sql import core_table
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
    """The pre-#327 path, run for real — the reference every assertion compares to.

    Calls the **production** `_top_values_per_column` rather than re-implementing
    it (#327 review, m3): a hand-rolled reference proves the batch matches the
    test's idea of the old path, not the path the fallback will actually run.
    """
    collected = profile_service._top_values_per_column(
        conn, schema="public", table=table, columns=columns, top_n=top_n, catalog=None, dialect=None
    )
    return {col: [dict(row) for row in rows] for col, rows in collected.items()}


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


@pytest.fixture
def alias_probe() -> Iterator[tuple[Any, str]]:
    """A table whose column names collide with the query's own output labels.

    `freq`, `value` and `rn` are the three names the top-values query labels
    something as — and SQL resolves a bare name in ``ORDER BY`` against the
    OUTPUT aliases first (#327 review, P2). Each column holds three distinct
    values tied at count 2, inserted in DESCENDING order, so a query whose
    tie-break collapsed to the count would keep a different pair than the one
    ``ROW_NUMBER()`` ranked — and the rows it kept would carry ranks past
    ``top_n``, join nothing, and vanish from the profile.
    """
    url = TEST_DATABASE_URL
    assert url is not None  # narrowed by the module-level skipif
    engine = create_engine(url)
    name = f"alias_probe_{uuid.uuid4().hex[:8]}"
    with engine.begin() as conn:
        conn.execute(text(f"CREATE TABLE public.{name} (freq integer, value text, rn integer)"))
        conn.execute(
            text(
                f"INSERT INTO public.{name} VALUES "
                "(30, 'c', 30), (30, 'c', 30), (20, 'b', 20),"
                "(20, 'b', 20), (10, 'a', 10), (10, 'a', 10)"
            )
        )
    try:
        with engine.connect() as conn:
            yield conn, name
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP TABLE public.{name}"))
        engine.dispose()


@pytest.mark.parametrize(
    ("col", "expected"),
    [
        ("freq", [10, 20]),
        ("value", ["a", "b"]),
        ("rn", [10, 20]),
    ],
)
def test_a_column_named_like_an_output_label_still_tie_breaks_by_value(
    alias_probe: tuple[Any, str], col: str, expected: list[Any]
) -> None:
    """#327 review, P2 — the whole point of the `dq_` label prefix.

    With three values tied at count 2 and ``top_n=2``, the answer is decided
    ENTIRELY by the value tie-break. If `ORDER BY count(*) DESC, freq` had
    resolved `freq` to the count alias, the pair kept would be arbitrary while
    the rank stayed value-ordered, and the mismatch shows up here as a missing
    or wrong value — not as an error.
    """
    conn, table = alias_probe
    batched = _batched(conn, table, [col], 2)
    assert [row["value"] for row in batched[col]] == expected
    assert [row["freq"] for row in batched[col]] == [2, 2]
    # …and the per-column path, which had the same latent capture, agrees.
    assert batched[col] == _per_column(conn, table, [col], 2)[col]


def _profile(conn: Any, table: str, monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> Any:
    """Run the real `profile_table` against `conn` (the live-connection seam faked)."""

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
        **overrides,
    }
    return profile_table(connection, **kwargs)


def _server_rejected_statement(*_args: Any, **_kwargs: Any) -> Any:
    """A statement the SERVER rejects — the only way to reproduce the P1 abort.

    A Python-side raise (monkeypatching the builder to throw) never reaches the
    database, so the transaction stays clean and the fallback runs on a healthy
    connection. That is precisely the test shape that hid the bug: this returns a
    perfectly valid `Select` over a table that does not exist, so Postgres
    rejects it mid-transaction and leaves the connection in the aborted state a
    real dialect rejection would.
    """
    return select(literal_column("1")).select_from(
        core_table(table="dq_no_such_table_327", schema="public", catalog=None)
    )


def test_profile_table_end_to_end_matches_the_per_column_path(
    probe_table: tuple[Any, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole service call over a real engine: batched vs fallback, same result.

    The fallback is forced with a statement the SERVER rejects, not a Python
    raise — see `_server_rejected_statement`.
    """
    conn, table = probe_table
    batched = _profile(conn, table, monkeypatch)

    monkeypatch.setattr(
        profile_service, "build_batched_top_values_query", _server_rejected_statement
    )
    fallback = _profile(conn, table, monkeypatch)

    assert batched == fallback
    amount = next(c for c in batched.columns if c.column == "amount")
    assert amount.top_values == [
        {"value": 1, "count": 2},
        {"value": 2, "count": 2},
        {"value": 3, "count": 1},
    ]
    assert next(c for c in batched.columns if c.column == "all_null").top_values == []


def test_fallback_survives_the_transaction_the_failed_batch_aborted(
    probe_table: tuple[Any, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#327 review, P1 — the safety net was dead code on transactional dialects.

    SQLAlchemy autobegins a transaction, and Postgres marks it ABORTED the moment
    a statement is rejected server-side: every later statement on that connection
    raises ``InFailedSqlTransaction`` until someone rolls back. So without
    `_recover_transaction` the per-column retry could not run at all, and the
    profile 502'd exactly where the pre-#327 code would have succeeded — the
    fallback failing in the only situation it exists for.

    Nothing in the earlier test suite could see it: the endpoint fake raised from
    Python before any SQL, and the pg test monkeypatched the builder to throw.
    This one lets the database do the rejecting, then insists the profile is not
    merely non-500 but *complete and correct*.
    """
    conn, table = probe_table
    reference = _profile(conn, table, monkeypatch)

    monkeypatch.setattr(
        profile_service, "build_batched_top_values_query", _server_rejected_statement
    )
    recovered = _profile(conn, table, monkeypatch)

    assert recovered == reference
    assert all(col.top_values or col.column == "all_null" for col in recovered.columns)
    # The connection is left usable, not poisoned for whatever runs next on it.
    assert conn.execute(select(literal_column("1"))).scalar() == 1
