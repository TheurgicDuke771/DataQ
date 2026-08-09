"""Make values safe to persist into PostgreSQL ``JSONB`` columns.

Great Expectations reports non-finite floats (``NaN``, ``Infinity``) inside
result payloads — e.g. a ``partial_unexpected_list`` of failing values, or an
``unexpected_percent`` on an empty batch. Python's ``json`` renders these as the
bare tokens ``NaN`` / ``Infinity``, which are not valid JSON and which Postgres
``JSONB`` rejects. ``sanitize_json`` walks a structure and replaces every
non-finite float with ``None`` — and coerces numpy scalars to native Python — so
GX results round-trip cleanly into the ``results`` table.

Most GX 1.17 results are native Python scalars, but the pandas (flat-file / Unity
Catalog) execution engine returns **numpy** scalars in some payloads — notably the
``unexpected_index_list`` identifier rows (#415), whose ``numpy.int64`` values are
not JSON-serializable and would fail the JSONB insert. ``.item()`` coerces any numpy
scalar to its Python equivalent before the finite-float check.

The warehouse (SQLAlchemy/Snowflake) execution engine returns ``decimal.Decimal``
for NUMERIC columns. A *passing* check never surfaces one (the failing-sample list
is empty), so this only fires when a range/threshold check on a NUMERIC column
genuinely fails — the observed-value/failing-sample payload then carries the raw
column values, and an unhandled ``Decimal`` crashes the whole result's JSONB insert
(#1273), silently discarding the run's results.
"""

from __future__ import annotations

import decimal
import math
from typing import Any


def sanitize_json(value: Any) -> Any:
    """Recursively coerce numpy scalars to native Python and replace non-finite
    floats with ``None``; leave the rest intact.

    Containers are rebuilt (dicts/lists); tuples become lists so the result is
    JSON-native. Scalars other than numpy/non-finite-float pass through unchanged.
    """
    # A numpy scalar (int64/float64/bool_/…) — duck-typed by `item`+`dtype` so `core`
    # takes no numpy import (matching profile_service._to_native, and keeping the slim
    # typecheck env clean). `.item()` yields the Python equivalent; a numpy float then
    # flows into the finite check below.
    if hasattr(value, "item") and hasattr(value, "dtype"):
        value = value.item()
    # pandas' missing-value sentinels: Arrow-backed frames (the iceberg native read,
    # #716) surface null cells to GX payloads as `pd.NA` / `pd.NaT`, neither of which
    # is JSON-serializable (#751). Duck-typed by type name — same no-pandas-import
    # stance as the numpy branch above; both are singletons of these exact types.
    # MUST precede the isoformat branch: NaT has .isoformat() but must become null.
    if type(value).__name__ in ("NAType", "NaTType"):
        return None
    # Dates/datetimes: GX coerces between-style kwargs to `datetime.date` in
    # `expected_value`, and Arrow-backed frames yield `pd.Timestamp` sample values —
    # JSON has no native form for either. `.isoformat()` mirrors
    # profile_service._to_native (found live in the #751 review).
    if hasattr(value, "isoformat"):
        return value.isoformat()
    # Warehouse NUMERIC columns (#1273) — `float()` then falls through to the
    # finite check below, so a Decimal NaN/Infinity is nulled the same as a float one.
    if isinstance(value, decimal.Decimal):
        value = float(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: sanitize_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_json(item) for item in value]
    return value
