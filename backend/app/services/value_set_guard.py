"""The redaction marker and the save-side guard for a withheld value set (#2592).

A leaf module (it imports no service), so the check writers can use it without joining the
import graph of the redaction ladder.
"""

from __future__ import annotations

from typing import Any

from backend.app.core.errors import DataQError

VALUE_SET_KEY = "value_set"
# Also the failing-sample sentinel (`run_service`); `core.logging` keeps its own copy.
REDACTED_MARKER = "<redacted>"


class ValueSetRedactedError(DataQError):
    status_code = 422
    code = "value_set_redacted"


def reject_masked_value_set(config: dict[str, Any] | None, *, check: str | None = None) -> None:
    """Refuse a config whose value set still holds the redaction marker: it came from a masked
    read, and saving it would replace the real values with the marker."""
    values = (config or {}).get(VALUE_SET_KEY)
    if isinstance(values, list) and REDACTED_MARKER in values:
        raise ValueSetRedactedError(
            "this value set holds withheld values (the column is classified as sensitive); "
            "send the full list of real values, or leave config out to keep the stored ones",
            detail={"field": f"config.{VALUE_SET_KEY}", **({"check": check} if check else {})},
        )
