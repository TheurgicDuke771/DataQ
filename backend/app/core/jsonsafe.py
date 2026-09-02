"""Make values safe to persist into PostgreSQL ``JSONB`` columns."""

from __future__ import annotations

import decimal
import json
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
    # raw bytes (or bytearray, depending on the DBAPI driver — core/artifacts.py and
    # lineage/dbt_manifest.py already treat the two as a pair; psycopg-style BYTEA
    # arrives as memoryview, #1729) — hex-encode rather than
    # leaving something JSON can't serialize at all (the flat-file profiler's own
    # `_to_native` has an equivalent str() catch-all; this is the SQL path's analogous
    # case, #1719 review).
    if isinstance(value, (bytes, bytearray, memoryview)):
        return value.hex()
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {_json_key(key): sanitize_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_json(item) for item in value]
    return value


def _json_key(key: Any) -> str:
    """Coerce a dict key to the string JSON requires (#1729).

    A `{column_value: count}` metric over a BINARY/NUMERIC column puts the same
    driver types in the KEY position that the value branches above handle — and
    a raw bytes/Decimal/numpy key crashes the whole JSONB insert. Keys take the
    same rendering as the equivalent value (bytes → hex, Decimal/numpy → native)
    and are then stringified the way ``json.dumps`` stringifies scalar keys
    (``True`` → ``"true"``, ``None`` → ``"null"``, ``1.5`` → ``"1.5"``).
    """
    if isinstance(key, str):
        return key
    sanitized = sanitize_json(key)
    if isinstance(sanitized, str):
        return sanitized
    if sanitized is None or isinstance(sanitized, (bool, int, float)):
        return json.dumps(sanitized)
    return str(sanitized)
