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
"""

from __future__ import annotations

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
    if type(value).__name__ in ("NAType", "NaTType"):
        return None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: sanitize_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_json(item) for item in value]
    return value
