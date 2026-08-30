"""Make values safe to persist into PostgreSQL ``JSONB`` columns."""

from __future__ import annotations

import decimal
import math
from typing import Any


def sanitize_json(value: Any) -> Any:
    """Recursively coerce numpy scalars to native Python and replace non-finite
    floats with ``None``; leave the rest intact.
    """
    # A numpy scalar (int64/float64/bool_/…) — duck-typed by `item`+`dtype` so `core` takes no numpy
    # import (matching profile_service._to_native, and keeping the slim typecheck env clean).
    if hasattr(value, "item") and hasattr(value, "dtype"):
        value = value.item()
    # pandas' missing-value sentinels: Arrow-backed frames (the iceberg native read, #716) surface
    # null cells to GX payloads as `pd.NA` / `pd.NaT`, neither of which is JSON-serializable (#751).
    if type(value).__name__ in ("NAType", "NaTType"):
        return None
    # Dates/datetimes: GX coerces between-style kwargs to `datetime.date` in `expected_value`, and
    # Arrow-backed frames yield `pd.Timestamp` sample values — JSON has no native form for either.
    if hasattr(value, "isoformat"):
        return value.isoformat()
    # Warehouse NUMERIC columns (#1273) — `float()` then falls through to the
    # finite check below, so a Decimal NaN/Infinity is nulled the same as a float one.
    if isinstance(value, decimal.Decimal):
        value = float(value)
    # Warehouse BINARY/VARBINARY columns (e.g. a profiled column's MIN/MAX) surface as
    # raw bytes — hex-encode rather than leaving something JSON can't serialize at all
    # (the flat-file profiler's own `_to_native` has an equivalent str() catch-all;
    # this is the SQL path's analogous case, #1719 review).
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: sanitize_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_json(item) for item in value]
    return value
