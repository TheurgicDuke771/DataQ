"""Make values safe to persist into PostgreSQL ``JSONB`` columns.

Great Expectations reports non-finite floats (``NaN``, ``Infinity``) inside
result payloads — e.g. a ``partial_unexpected_list`` of failing values, or an
``unexpected_percent`` on an empty batch. Python's ``json`` renders these as the
bare tokens ``NaN`` / ``Infinity``, which are not valid JSON and which Postgres
``JSONB`` rejects. ``sanitize_json`` walks a structure and replaces every
non-finite float with ``None`` so GX results round-trip cleanly into the
``results`` table.

GX 1.17 returns native Python scalars (not numpy types), so only ``float`` needs
special handling here.
"""

from __future__ import annotations

import math
from typing import Any


def sanitize_json(value: Any) -> Any:
    """Recursively replace non-finite floats with ``None``; leave the rest intact.

    Containers are rebuilt (dicts/lists); tuples become lists so the result is
    JSON-native. Scalars other than non-finite floats pass through unchanged.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: sanitize_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_json(item) for item in value]
    return value
