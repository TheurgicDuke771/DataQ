"""Column-profiler unit tests — pure, no DB / no warehouse.

Covers the injection-safe identifier quoting, the SQL builders, and the
result assembly (`assemble_profile`) from canned query rows. The live I/O seam
(`_open_connection`) is exercised via the endpoint tests with a fake connection.
"""

import math

import pytest

from backend.app.services.profile_service import (
    ProfileIdentifierInvalidError,
    assemble_profile,
    build_aggregate_query,
    build_top_values_query,
    quote_identifier,
)

# ── quote_identifier ──


@pytest.mark.parametrize("name", ["id", "_x", "Col1", "amount$usd", "ORDERS"])
def test_quote_identifier_accepts_plain_identifiers(name: str) -> None:
    assert quote_identifier(name) == f'"{name}"'


@pytest.mark.parametrize(
    "bad",
    ["a b", 'a"b', "a;b", "a.b", "a-b", "1col", "", "a)b", "a'b", "a b; DROP TABLE x"],
)
def test_quote_identifier_rejects_unsafe(bad: str) -> None:
    with pytest.raises(ProfileIdentifierInvalidError):
        quote_identifier(bad)


def test_quote_identifier_rejects_none() -> None:
    with pytest.raises(ProfileIdentifierInvalidError):
        quote_identifier(None)


# ── query builders ──


def test_aggregate_query_quotes_and_aliases_per_column() -> None:
    sql = build_aggregate_query("public", "orders", ["amount", "status"])
    assert sql.startswith("SELECT COUNT(*) AS row_count,")
    assert 'FROM "public"."orders"' in sql
    # positional aliases per column, quoted identifiers
    for token in ("nulls_0", "distinct_0", "min_0", "max_0", "nulls_1", "max_1"):
        assert token in sql
    assert 'COUNT(DISTINCT "amount")' in sql and 'MAX("status")' in sql


def test_top_values_query_orders_and_limits() -> None:
    sql = build_top_values_query("public", "orders", "status", 5)
    assert 'SELECT "status" AS value, COUNT(*) AS freq FROM "public"."orders"' in sql
    assert 'WHERE "status" IS NOT NULL GROUP BY "status"' in sql
    assert "ORDER BY freq DESC, value LIMIT 5" in sql


def test_builders_reject_unsafe_identifiers() -> None:
    with pytest.raises(ProfileIdentifierInvalidError):
        build_aggregate_query("public", "orders; DROP TABLE x", ["amount"])
    with pytest.raises(ProfileIdentifierInvalidError):
        build_top_values_query("public", "orders", "amount; --", 5)


# ── assemble_profile ──


def test_assemble_computes_null_fraction_and_maps_top_values() -> None:
    aggregate = {
        "row_count": 100,
        "nulls_0": 25,
        "distinct_0": 4,
        "min_0": 1,
        "max_0": 9,
    }
    top_values = {"amount": [{"value": 9, "freq": 40}, {"value": 1, "freq": 35}]}
    profile = assemble_profile(
        table="orders",
        schema="public",
        columns=["amount"],
        aggregate=aggregate,
        top_values=top_values,
    )
    assert profile.row_count == 100
    col = profile.columns[0]
    assert col.null_count == 25
    assert col.null_fraction == 0.25
    assert col.distinct_count == 4
    assert col.min_value == 1 and col.max_value == 9
    assert col.top_values == [{"value": 9, "count": 40}, {"value": 1, "count": 35}]


def test_assemble_empty_table_has_zero_null_fraction_not_div_by_zero() -> None:
    aggregate = {"row_count": 0, "nulls_0": 0, "distinct_0": 0, "min_0": None, "max_0": None}
    profile = assemble_profile(
        table="t", schema="s", columns=["c"], aggregate=aggregate, top_values={}
    )
    assert profile.row_count == 0
    assert profile.columns[0].null_fraction == 0.0
    assert profile.columns[0].top_values == []


def test_assemble_sanitizes_nan_min_max() -> None:
    aggregate = {
        "row_count": 3,
        "nulls_0": 0,
        "distinct_0": 3,
        "min_0": float("nan"),
        "max_0": 9.0,
    }
    profile = assemble_profile(
        table="t", schema="s", columns=["c"], aggregate=aggregate, top_values={}
    )
    # NaN → None (JSON-safe); a real number is untouched
    assert profile.columns[0].min_value is None
    assert profile.columns[0].max_value == 9.0
    assert not isinstance(profile.columns[0].min_value, float) or not math.isnan(
        profile.columns[0].min_value
    )
