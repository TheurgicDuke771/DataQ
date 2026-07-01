"""Tests for the JSONB sanitizer."""

import json
import math

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
    import numpy as np

    # GX's pandas engine returns numpy scalars in unexpected_index_list (#415); they
    # aren't JSON-serializable and would break the JSONB persist.
    cleaned = sanitize_json(
        {"unexpected_index_list": [{"order_id": np.int64(2), "qty": np.float64(-5.0)}]}
    )
    assert cleaned == {"unexpected_index_list": [{"order_id": 2, "qty": -5.0}]}
    json.dumps(cleaned, allow_nan=False)  # round-trips cleanly
    assert type(cleaned["unexpected_index_list"][0]["order_id"]) is int


def test_numpy_nan_becomes_none() -> None:
    import numpy as np

    assert sanitize_json(np.float64("nan")) is None
