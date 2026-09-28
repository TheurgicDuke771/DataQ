"""Make values safe to persist into PostgreSQL ``JSONB`` columns."""

from __future__ import annotations

import datetime
import decimal
import json
import math
import re
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
        raw = value
        value = value.item()
        if getattr(raw.dtype, "kind", None) == "M" and not hasattr(value, "isoformat"):
            # np.datetime64 finer than µs `.item()`s to an int (ns since the epoch, #1803),
            # not a datetime — narrow to µs first. NaT `.item()`s to None at every unit.
            value = raw.astype("datetime64[us]").item()
        elif getattr(raw.dtype, "kind", None) == "m":
            # np.timedelta64 `.item()`s to a bare int below µs and loses precision/overflows via
            # timedelta: rendered exactly from its integer count and unit instead (#1819).
            return None if value is None else _timedelta64_iso(raw)
    # pandas' missing-value sentinels: Arrow-backed frames (the iceberg native read, #716) surface
    # null cells to GX payloads as `pd.NA` / `pd.NaT`, neither of which is JSON-serializable (#751).
    if type(value).__name__ in ("NAType", "NaTType"):
        return None
    # Dates/datetimes: GX coerces between-style kwargs to `datetime.date` in `expected_value`, and
    # Arrow-backed frames yield `pd.Timestamp` sample values — JSON has no native form for either.
    if hasattr(value, "isoformat"):
        return value.isoformat()
    # A duration has no JSON form either: spelled as pandas' own `Timedelta.isoformat` (#1819), so
    # the same duration reads the same whichever library carried it.
    if isinstance(value, datetime.timedelta):
        return iso_duration(value)
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


_NS_PER: dict[str, int] = {
    "ns": 1,
    "us": 10**3,
    "ms": 10**6,
    "s": 10**9,
    "m": 60 * 10**9,
    "h": 3600 * 10**9,
    "D": 86400 * 10**9,
    "W": 7 * 86400 * 10**9,
}
_TIMEDELTA64_UNIT = re.compile(r"timedelta64\[(\d*)(\w+)\]")


def iso_duration(value: datetime.timedelta) -> str:
    """ISO-8601 duration in pandas' spelling: signed days, then non-negative time of day."""
    micros = (value.days * 86400 + value.seconds) * 10**6 + value.microseconds
    return _iso_from_ns(micros * 1000)


def _timedelta64_iso(raw: Any) -> str:
    match = _TIMEDELTA64_UNIT.fullmatch(raw.dtype.name)
    if match is None or match.group(2) not in _NS_PER:
        # Calendar units (months, years) have no fixed length: numpy's own average-length cast.
        raw = raw.astype("timedelta64[ns]")
        match = _TIMEDELTA64_UNIT.fullmatch(raw.dtype.name)
        assert match is not None  # nosec B101
    step = int(match.group(1) or 1) * _NS_PER[match.group(2)]
    return _iso_from_ns(int(raw.view("i8")) * step)


def _iso_from_ns(total: int) -> str:
    days, rest = divmod(total, 86400 * 10**9)
    hours, rest = divmod(rest, 3600 * 10**9)
    minutes, rest = divmod(rest, 60 * 10**9)
    seconds, nanos = divmod(rest, 10**9)
    fraction = f".{nanos:09d}".rstrip("0") if nanos else ""
    return f"P{days}DT{hours}H{minutes}M{seconds}{fraction}S"


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
