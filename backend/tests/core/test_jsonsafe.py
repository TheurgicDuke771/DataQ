"""Tests for the JSONB sanitizer."""

import datetime
import decimal
import json
import math

import numpy as np
import pandas as pd
import pytest

from backend.app.core.jsonsafe import sanitize_json


def test_nan_becomes_none() -> None:
    assert sanitize_json(float("nan")) is None


def test_infinities_become_none() -> None:
    assert sanitize_json(float("inf")) is None
    assert sanitize_json(float("-inf")) is None


def test_finite_floats_pass_through() -> None:
    assert sanitize_json(0.5) == 0.5
    assert sanitize_json(0.0) == 0.0
    assert sanitize_json(-3.14) == -3.14


def test_non_floats_pass_through() -> None:
    assert sanitize_json(3) == 3
    assert sanitize_json("id") == "id"
    assert sanitize_json(True) is True
    assert sanitize_json(None) is None


def test_nested_structure_is_sanitized() -> None:
    # Mirrors a real GX result fragment: a sample list with a NaN.
    payload = {
        "unexpected_count": 1,
        "unexpected_percent": float("nan"),
        "partial_unexpected_list": [None, float("nan"), 2.0],
        "observed_value": 3,
    }
    cleaned = sanitize_json(payload)
    assert cleaned == {
        "unexpected_count": 1,
        "unexpected_percent": None,
        "partial_unexpected_list": [None, None, 2.0],
        "observed_value": 3,
    }


def test_result_is_strict_json_serialisable() -> None:
    """The whole point: output must serialise with allow_nan=False (JSONB-safe)."""
    payload = {"sample": [float("nan"), float("inf")], "n": 2}
    cleaned = sanitize_json(payload)
    # allow_nan=False raises ValueError if any NaN/Infinity survived.
    json.dumps(cleaned, allow_nan=False)


def test_tuples_become_lists() -> None:
    result = sanitize_json((1.0, float("nan"), 3))
    assert result == [1.0, None, 3]


def test_does_not_mutate_input() -> None:
    original = {"x": [float("nan")]}
    sanitize_json(original)
    assert math.isnan(original["x"][0])  # input untouched


def test_numpy_scalars_are_coerced_to_native() -> None:

    # GX's pandas engine returns numpy scalars in unexpected_index_list (#415); they
    # aren't JSON-serializable and would break the JSONB persist.
    cleaned = sanitize_json(
        {"unexpected_index_list": [{"order_id": np.int64(2), "qty": np.float64(-5.0)}]}
    )
    assert cleaned == {"unexpected_index_list": [{"order_id": 2, "qty": -5.0}]}
    json.dumps(cleaned, allow_nan=False)  # round-trips cleanly
    assert type(cleaned["unexpected_index_list"][0]["order_id"]) is int


def test_numpy_nan_becomes_none() -> None:

    assert sanitize_json(np.float64("nan")) is None


def test_pandas_na_and_nat_become_none() -> None:

    # Arrow-backed frames (the iceberg native read, #716) surface null cells to GX payloads as pd.NA
    # / pd.NaT.
    cleaned = sanitize_json({"partial_unexpected_list": [pd.NA, "SUP-0001"], "last_seen": pd.NaT})
    assert cleaned == {"partial_unexpected_list": [None, "SUP-0001"], "last_seen": None}
    json.dumps(cleaned, allow_nan=False)  # round-trips cleanly


def test_decimal_becomes_float() -> None:

    # Warehouse (SQLAlchemy/Snowflake) NUMERIC columns surface as decimal.Decimal in a failing
    # check's observed-value/failing-sample payload (#1273, reproduced live on a Snowflake range
    # check that genuinely failed — a passing check never surfaces one, which is why this went
    # unnoticed).
    cleaned = sanitize_json({"observed_value": decimal.Decimal("1234.56")})
    assert cleaned == {"observed_value": 1234.56}
    assert type(cleaned["observed_value"]) is float
    json.dumps(cleaned, allow_nan=False)  # round-trips cleanly


def test_decimal_nan_and_infinity_become_none() -> None:

    assert sanitize_json(decimal.Decimal("NaN")) is None
    assert sanitize_json(decimal.Decimal("Infinity")) is None
    assert sanitize_json(decimal.Decimal("-Infinity")) is None


def test_nested_decimal_list_is_sanitized() -> None:

    # Mirrors the real crash: a failing_sample list of raw NUMERIC column values.
    payload = {"partial_unexpected_list": [decimal.Decimal("100.00"), decimal.Decimal("250.5")]}
    cleaned = sanitize_json(payload)
    assert cleaned == {"partial_unexpected_list": [100.00, 250.5]}
    json.dumps(cleaned, allow_nan=False)


def test_bytes_become_hex() -> None:
    # A Snowflake/Databricks BINARY/VARBINARY column's MIN/MAX (e.g. the LLM
    # check-suggestion prompt's profile stats, #1719 review) surfaces as raw
    # bytes — json.dumps has no native form for it at all.
    cleaned = sanitize_json({"min_value": b"\x01\x02\x8f", "max_value": b"\xff\x00"})
    assert cleaned == {"min_value": "01028f", "max_value": "ff00"}
    json.dumps(cleaned, allow_nan=False)  # round-trips cleanly


def test_nested_bytes_list_is_sanitized() -> None:
    cleaned = sanitize_json({"partial_unexpected_list": [b"\x00", b"\x01"]})
    assert cleaned == {"partial_unexpected_list": ["00", "01"]}


def test_bytearray_becomes_hex() -> None:
    # Some DBAPI drivers hand back bytearray rather than bytes for a BINARY
    # column — core/artifacts.py and lineage/dbt_manifest.py already treat the
    # two as a pair, so sanitize_json must too (#1719 review).
    cleaned = sanitize_json({"min_value": bytearray(b"\x01\x02")})
    assert cleaned == {"min_value": "0102"}
    json.dumps(cleaned, allow_nan=False)


def test_timestamps_and_dates_become_isoformat() -> None:

    import pandas as pd

    # Arrow-backed frames yield pd.Timestamp sample values, and GX coerces between-style kwargs
    # into datetime.date in expected_value — JSON has no native form for either (#751 review, both
    # reproduced live).
    cleaned = sanitize_json(
        {
            "partial_unexpected_list": [pd.Timestamp("2099-01-01 00:00:00")],
            "min_value": datetime.date(2019, 1, 1),
            "seen": datetime.datetime(2026, 7, 10, 6, 30, 0),
            "missing": pd.NaT,
        }
    )
    assert cleaned == {
        "partial_unexpected_list": ["2099-01-01T00:00:00"],
        "min_value": "2019-01-01",
        "seen": "2026-07-10T06:30:00",
        "missing": None,
    }
    json.dumps(cleaned, allow_nan=False)


def test_memoryview_becomes_hex() -> None:
    # psycopg-style BYTEA surfaces as memoryview (#1729) — hex, like bytes/bytearray.
    cleaned = sanitize_json({"min_value": memoryview(b"\x01\x02")})
    assert cleaned == {"min_value": "0102"}
    json.dumps(cleaned, allow_nan=False)


# Every type the value branch handles, used in the KEY position of a
# `{column_value: count}` histogram (#1729): (raw key, expected str key, is json-legal raw).
# bytearray is unhashable so can never reach the key position.
_DRIVER_KEYS = [
    pytest.param(b"\x01\x02\x8f", "01028f", False, id="bytes"),
    pytest.param(memoryview(b"\x0a"), "0a", False, id="memoryview"),
    pytest.param(decimal.Decimal("1234.56"), "1234.56", False, id="decimal"),
    pytest.param(decimal.Decimal("100"), "100.0", False, id="decimal-integral"),
    pytest.param(np.int64(7), "7", False, id="np-int64"),
    pytest.param(np.float64(2.5), "2.5", True, id="np-float64"),  # a float subclass
    pytest.param(np.bool_(True), "true", False, id="np-bool"),
    pytest.param(np.str_("col"), "col", True, id="np-str"),
    pytest.param(True, "true", True, id="bool-true"),
    pytest.param(False, "false", True, id="bool-false"),
    pytest.param(None, "null", True, id="none"),
    pytest.param(3, "3", True, id="int"),
    pytest.param(-1.5, "-1.5", True, id="float"),
    pytest.param(datetime.date(2019, 1, 1), "2019-01-01", False, id="date"),
    pytest.param(pd.Timestamp("2026-01-01"), "2026-01-01T00:00:00", False, id="pd-timestamp"),
    pytest.param(pd.NA, "null", False, id="pd-na"),
    pytest.param((1, 2), "[1, 2]", False, id="tuple"),
    pytest.param("plain", "plain", True, id="str"),
]


@pytest.mark.parametrize(("raw", "expected", "_json_legal"), _DRIVER_KEYS)
def test_dict_key_is_sanitized_to_json_string(
    raw: object, expected: str, _json_legal: bool
) -> None:
    cleaned = sanitize_json({raw: 1})
    assert cleaned == {expected: 1}
    assert type(next(iter(cleaned))) is str  # np.str_ normalised too
    json.dumps(cleaned, allow_nan=False)


@pytest.mark.parametrize(("raw", "_expected", "json_legal"), _DRIVER_KEYS)
def test_raw_driver_key_json_legality_is_as_declared(
    raw: object, _expected: str, json_legal: bool
) -> None:
    # Guards the table itself: a case marked illegal really would have crashed the encoder.
    if json_legal:
        json.dumps({raw: 1})
    else:
        with pytest.raises(TypeError):
            json.dumps({raw: 1})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(float("nan"), "NaN", id="nan"),
        pytest.param(float("inf"), "Infinity", id="inf"),
        pytest.param(float("-inf"), "-Infinity", id="-inf"),
        pytest.param(np.float64("nan"), "NaN", id="np-nan"),
        pytest.param(decimal.Decimal("Infinity"), "Infinity", id="decimal-inf"),
    ],
)
def test_non_finite_float_keys_keep_json_spellings(raw: object, expected: str) -> None:
    # The value branch nulls these, but json.dumps rendered NaN/±Infinity KEYS distinctly
    # before #1729 — routing keys through the null would merge the buckets (review finding).
    assert sanitize_json({raw: 1}) == {expected: 1}


def test_non_finite_float_keys_do_not_collide() -> None:
    cleaned = sanitize_json({float("nan"): 1, float("inf"): 2, float("-inf"): 3})
    assert cleaned == {"NaN": 1, "Infinity": 2, "-Infinity": 3}
    json.dumps(cleaned, allow_nan=False)


def test_unknown_key_type_raises_like_the_encoder() -> None:
    # Types the value branch passes through untouched still fail loudly as keys — the
    # sanitizer handles known driver types, it does not paper over arbitrary objects.
    with pytest.raises(TypeError):
        sanitize_json({frozenset({1}): 1})


def test_nested_dict_keys_are_sanitized() -> None:
    payload = {"value_counts": {b"\x00": 3, decimal.Decimal("9.5"): 1, "plain": 2}}
    cleaned = sanitize_json(payload)
    assert cleaned == {"value_counts": {"00": 3, "9.5": 1, "plain": 2}}
    json.dumps(cleaned, allow_nan=False)
