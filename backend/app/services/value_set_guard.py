"""The redaction marker and the save-side guard for a withheld value set (#2592).

A leaf module (it imports no service), so the check writers can use it without joining the
import graph of the redaction ladder.
"""

from __future__ import annotations

from typing import Any

from backend.app.core.errors import DataQError

VALUE_SET_KEY = "value_set"
#: The config keys that hold values of the tested column on every check type: sets, pairs and
#: patterns, in a list or alone (#2682), and a DQX rule's allowed list and limits (#2691).
VALUE_KEYS: tuple[str, ...] = (
    VALUE_SET_KEY,
    "value_pairs_set",
    "regex",
    "regex_list",
    "like_pattern",
    "like_pattern_list",
    "allowed",
    "min_limit",
    "max_limit",
    "limit",
)
#: Bounds, which are values of the column only on some check types (`bound_keys_hold_values`):
#: an aggregate monitor's tiered bounds beside its `min_value` / `max_value` (#2691).
BOUND_KEYS: tuple[str, ...] = (
    "min_value",
    "max_value",
    "critical_min",
    "warn_min",
    "critical_max",
    "warn_max",
)
# Also the failing-sample sentinel (`run_service`); `core.logging` keeps its own copy.
REDACTED_MARKER = "<redacted>"


class ValueSetRedactedError(DataQError):
    status_code = 422
    code = "value_set_redacted"


def holds_marker(value: Any) -> bool:
    """Whether ``value`` is, or holds anywhere inside it, the redaction marker."""
    if isinstance(value, list):
        return any(holds_marker(item) for item in value)
    if isinstance(value, dict):
        return any(holds_marker(item) for item in value.values())
    return bool(value == REDACTED_MARKER)


def reject_masked_value_set(config: dict[str, Any] | None, *, check: str | None = None) -> None:
    """Refuse a config whose values still hold the redaction marker: it came from a masked
    read, and saving it would replace the real values (a set, a pattern, a bound) with the
    marker."""
    for key in (*VALUE_KEYS, *BOUND_KEYS):
        if holds_marker((config or {}).get(key)):
            raise ValueSetRedactedError(
                "this check holds withheld values (the column is classified as sensitive); "
                "send the real values, or leave config out to keep the stored ones",
                detail={"field": f"config.{key}", **({"check": check} if check else {})},
            )
