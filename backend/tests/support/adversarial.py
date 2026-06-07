"""Adversarial-input fixtures + a JSON-safety contract for data-ingesting code.

Most of our bugs in data-handling code were *not* missing coverage — the lines
ran, just never with hostile data. A profiler/runner that eats arbitrary user
files has to survive columns it can't compare (mixed types), can't hash (nested
cells), or can't JSON-encode (NaN/Inf, bytes). This module centralises that
hostile-input set so any function that processes a DataFrame can be swept with
the same battery, and asserts the one contract that matters at the edge: the
output is **plain JSON** (no NaN/Inf, no exotic scalars) and never raised.

`ADVERSARIAL_FRAMES` is `(id, DataFrame)` pairs, each a single column named
``x``. Pass it to `pytest.mark.parametrize`. `assert_json_safe(value)` enforces
the JSON contract on any emitted scalar/structure.
"""

from __future__ import annotations

import io
import json
from typing import Any

import pandas as pd


def _parquet_roundtrip(frame: pd.DataFrame) -> pd.DataFrame:
    """Round-trip through Parquet with the Arrow backend the profiler/runner use.

    Arrow-backed columns raise *different* exception types than numpy ones (e.g.
    ``ArrowNotImplementedError`` vs ``TypeError``), so the Arrow variants are the
    ones that catch over-narrow ``except`` clauses.
    """
    buf = io.BytesIO()
    frame.to_parquet(buf)
    buf.seek(0)
    return pd.read_parquet(buf, dtype_backend="pyarrow")


ADVERSARIAL_FRAMES: list[tuple[str, pd.DataFrame]] = [
    # — object columns the numpy way —
    ("mixed_int_str", pd.DataFrame({"x": [10, "N/A", 20, "N/A"]})),
    ("mixed_with_none", pd.DataFrame({"x": [1, None, "x", 2.5]})),
    ("all_null", pd.DataFrame({"x": [None, None, None]})),
    ("empty_rows", pd.DataFrame({"x": pd.Series([], dtype="object")})),
    ("unhashable_list_numpy", pd.DataFrame({"x": [[1], [2], [1]]})),
    ("unhashable_dict", pd.DataFrame({"x": [{"a": 1}, {"b": 2}]})),
    # — floats / non-finite —
    ("nan_inf", pd.DataFrame({"x": [1.0, float("nan"), float("inf"), float("-inf")]})),
    ("all_nan", pd.DataFrame({"x": [float("nan"), float("nan")]})),
    # — exotic-but-real scalar types from real files —
    ("unicode", pd.DataFrame({"x": ["café", "naïve", "🦄", "x"]})),
    ("bytes_values", pd.DataFrame({"x": [b"\x00\x01", b"\xff", b"\x00\x01"]})),
    ("big_ints", pd.DataFrame({"x": [10**30, -(10**30), 0]})),
    ("bools", pd.DataFrame({"x": [True, False, True, None]})),
    ("datetimes", pd.DataFrame({"x": pd.to_datetime(["2026-01-01", "2026-06-06", None])})),
    # — Arrow-backed (Parquet) variants: different exception surface —
    ("arrow_list", _parquet_roundtrip(pd.DataFrame({"x": [[1, 2], [3], [1, 2]]}))),
    ("arrow_struct", _parquet_roundtrip(pd.DataFrame({"x": [{"a": 1}, {"a": 2}, {"a": 1}]}))),
    ("arrow_ints", _parquet_roundtrip(pd.DataFrame({"x": [1, 2, 2, None]}))),
    ("arrow_strings", _parquet_roundtrip(pd.DataFrame({"x": ["a", "b", "a", None]}))),
]


def assert_json_safe(value: Any) -> None:
    """Assert `value` is plain JSON — no NaN/Inf, no types `json` can't encode.

    `allow_nan=False` rejects the NaN/Inf the sanitiser is supposed to have
    stripped; the default encoder rejects bytes / Decimal / numpy / datetime, so
    a leaked exotic scalar fails here instead of at the HTTP boundary.
    """
    json.dumps(value, allow_nan=False)
