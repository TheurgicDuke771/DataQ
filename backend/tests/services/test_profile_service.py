"""Column-profiler unit tests — pure, no DB / no warehouse.

Covers identifier validation, the SQLAlchemy Core query builders (compiled to
SQL for inspection), SQL result assembly (`assemble_profile`), and the flat-file
`profile_dataframe` / `infer_file_format` helpers. The live I/O seams
(`_open_connection`, `_read_dataframe`) are exercised via the endpoint tests.
"""

import math

import pandas as pd
import pytest

from backend.app.services.profile_service import (
    ProfileColumnNotFoundError,
    ProfileIdentifierInvalidError,
    ProfileTargetInvalidError,
    assemble_profile,
    build_aggregate_query,
    build_top_values_query,
    infer_file_format,
    profile_dataframe,
    validate_identifier,
)


def _sql(stmt: object) -> str:
    """Compile a Core statement to literal SQL, lowercased, for assertions."""
    return str(stmt.compile(compile_kwargs={"literal_binds": True})).lower()  # type: ignore[attr-defined]


# ── validate_identifier ──


@pytest.mark.parametrize("name", ["id", "_x", "Col1", "amount$usd", "ORDERS"])
def test_validate_identifier_accepts_plain_identifiers(name: str) -> None:
    assert validate_identifier(name) == name


@pytest.mark.parametrize(
    "bad",
    ["a b", 'a"b', "a;b", "a.b", "a-b", "1col", "", "a)b", "a'b", "a b; DROP TABLE x"],
)
def test_validate_identifier_rejects_unsafe(bad: str) -> None:
    with pytest.raises(ProfileIdentifierInvalidError):
        validate_identifier(bad)


def test_validate_identifier_rejects_none() -> None:
    with pytest.raises(ProfileIdentifierInvalidError):
        validate_identifier(None)


# ── query builders (compiled SQL inspection) ──


def test_aggregate_query_aggregates_and_labels_per_column() -> None:
    sql = _sql(build_aggregate_query("public", "orders", ["amount", "status"]))
    assert "count(*) as row_count" in sql
    assert "from public.orders" in sql
    # positional labels per column
    for token in ("nulls_0", "distinct_0", "min_0", "max_0", "nulls_1", "max_1"):
        assert token in sql
    assert "count(distinct amount)" in sql and "max(status)" in sql


def test_top_values_query_orders_and_limits() -> None:
    sql = _sql(build_top_values_query("public", "orders", "status", 5))
    assert "status as value" in sql and "count(*) as freq" in sql
    assert "from public.orders" in sql
    assert "where status is not null" in sql and "group by status" in sql
    assert "order by count(*) desc" in sql
    assert "limit 5" in sql


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


# ── infer_file_format ──


@pytest.mark.parametrize(
    ("path", "explicit", "expected"),
    [
        ("data/orders.csv", None, "csv"),
        ("DATA/ORDERS.CSV", None, "csv"),
        ("x.parquet", None, "parquet"),
        ("x.pq", None, "parquet"),
        ("data/blob", "csv", "csv"),
        ("data/orders.csv", "parquet", "parquet"),  # explicit overrides extension
    ],
)
def test_infer_file_format(path: str, explicit: str | None, expected: str) -> None:
    assert infer_file_format(path, explicit) == expected


@pytest.mark.parametrize("path", ["data/orders.xml", "data/blob", "noext"])
def test_infer_file_format_unknown_raises(path: str) -> None:
    with pytest.raises(ProfileTargetInvalidError):
        infer_file_format(path, None)


# ── profile_dataframe ──


def test_profile_dataframe_computes_stats() -> None:
    df = pd.DataFrame({"amount": [10, 20, 20, 20], "city": ["x", "x", "y", None]})
    result = profile_dataframe(
        df, columns=["amount", "city"], top_n=5, path="f.csv", file_format="csv"
    )
    assert result.row_count == 4 and result.path == "f.csv" and result.file_format == "csv"
    amount = result.columns[0]
    assert amount.null_count == 0 and amount.distinct_count == 2
    assert amount.min_value == 10 and amount.max_value == 20
    assert amount.top_values[0] == {"value": 20, "count": 3}
    city = result.columns[1]
    assert city.null_count == 1 and city.null_fraction == 0.25
    assert city.min_value == "x" and city.max_value == "y"


def test_profile_dataframe_missing_column_raises() -> None:
    df = pd.DataFrame({"a": [1]})
    with pytest.raises(ProfileColumnNotFoundError):
        profile_dataframe(df, columns=["a", "missing"], top_n=5, path="f.csv", file_format="csv")


def test_profile_dataframe_all_null_column_has_none_min_max() -> None:
    df = pd.DataFrame({"a": [None, None]})
    result = profile_dataframe(df, columns=["a"], top_n=5, path="f.csv", file_format="csv")
    col = result.columns[0]
    assert col.null_count == 2 and col.null_fraction == 1.0
    assert col.distinct_count == 0
    assert col.min_value is None and col.max_value is None
    assert col.top_values == []


def test_profile_dataframe_coerces_timestamps_to_iso() -> None:
    df = pd.DataFrame({"ts": pd.to_datetime(["2026-01-01", "2026-06-06"])})
    result = profile_dataframe(df, columns=["ts"], top_n=5, path="f.parquet", file_format="parquet")
    col = result.columns[0]
    assert col.min_value == "2026-01-01T00:00:00"
    assert col.max_value == "2026-06-06T00:00:00"
    assert col.top_values[0]["value"].startswith("2026-")


# ── _read_dataframe column projection (real parse, mocked download) ──


def test_read_dataframe_csv_projects_only_requested_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.services import profile_service as svc

    monkeypatch.setattr(svc, "_download_bytes", lambda *a, **k: b"a,b,c\n1,2,3\n4,5,6\n")
    df = svc._read_dataframe(
        object(), path="x.csv", file_format="csv", columns=["a", "c"], secret_store=object()
    )
    assert list(df.columns) == ["a", "c"]  # 'b' is never parsed
    assert len(df) == 2


def test_read_dataframe_parquet_projects_only_requested_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import io

    from backend.app.services import profile_service as svc

    buf = io.BytesIO()
    pd.DataFrame({"a": [1, 2], "b": [3, 4], "c": [5, 6]}).to_parquet(buf)
    monkeypatch.setattr(svc, "_download_bytes", lambda *a, **k: buf.getvalue())
    df = svc._read_dataframe(
        object(), path="x.parquet", file_format="parquet", columns=["a", "c"], secret_store=object()
    )
    assert set(df.columns) == {"a", "c"}  # 'b' is never read
    assert len(df) == 2


def test_to_native_handles_none_and_nan() -> None:
    from backend.app.services.profile_service import _to_native

    assert _to_native(None) is None
    assert _to_native(float("nan")) is None
    assert _to_native(5) == 5
