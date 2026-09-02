"""Make values safe to persist into PostgreSQL ``JSONB`` columns."""

from __future__ import annotations

import decimal
import json
import math
from typing import Any

#: What a BINARY/VARBINARY/BYTEA column surfaces as, by DBAPI driver: bytes (databricks-sql),
#: bytearray (snowflake-connector), memoryview (psycopg).
BINARY_TYPES = (bytes, bytearray, memoryview)


def bytes_to_hex(value: bytes | bytearray | memoryview) -> str:
    """The one rendering of a binary column value, shared by every persistence and
    display path (#1721) so the same bytes never read differently by datasource family.
    """
    return value.hex()


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
    if isinstance(value, BINARY_TYPES):
        return bytes_to_hex(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {_json_key(key): sanitize_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_json(item) for item in value]
    return value


def _json_key(key: Any) -> str:
    """Coerce a dict key to the string JSON requires (#1729).

    Defensive: no producer keys a dict by data values today, but a
    `{column_value: count}` histogram over a BINARY/NUMERIC column would put the
    driver types the value branches handle into the KEY position, where a raw
    bytes/Decimal/numpy key crashes the whole JSONB insert. A key gets the value
    branch's rendering (bytes → hex, Decimal/numpy → native) and is then written
    as JSON text — for scalars exactly what ``json.dumps`` renders a key as
    (``True`` → ``"true"``, ``1.5`` → ``"1.5"``). Types the value branch does not
    know raise ``TypeError`` here as they would in the encoder.
    """
    sanitized = sanitize_json(key)
    if sanitized is None and key is not None:
        # The value branch nulls a non-finite float, but as keys NaN and ±Infinity
        # would then merge into one bucket — keep json's own distinct spellings.
        try:
            return json.dumps(float(key))
        except TypeError:
            pass  # pd.NA / pd.NaT — genuinely null
    return sanitized if isinstance(sanitized, str) else json.dumps(sanitized)
