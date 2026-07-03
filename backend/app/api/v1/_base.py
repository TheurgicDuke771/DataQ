"""Shared request/response model base for the v1 API.

Every model in `backend/app/api/v1/` inherits `ApiModel` instead of Pydantic's
`BaseModel` so cross-cutting input contracts live in exactly one place.

The one contract today: **no NUL (``\\x00``) anywhere in a payload** (#567).
Pydantic's `str` accepts NUL, but Postgres rejects it at INSERT time for both
text columns and JSONB — so a hostile/binary-contaminated string in any
persisted free-text field (suite/check/connection names, descriptions, nested
check ``config`` values, import documents …) would otherwise surface as a
driver ``ValueError`` → an unhandled 500 instead of a 422. Rejecting it at the
validation boundary keeps the "bad input is never a 500" error-envelope
guarantee and names the offending model in the standard validation error.

The walk runs on the *raw* inbound payload (``mode="before"``), so nested
dicts/lists — including dict *keys* — are covered before field parsing.
Response models constructed from ORM objects pass through untouched (the
walk only descends str/dict/list/tuple/set; DB data cannot contain NUL).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, model_validator


def contains_nul(value: Any) -> bool:
    """True if a NUL (``\\x00``) appears in any string within ``value``
    (recursing through dict keys/values and list/tuple/set items)."""
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        return any(contains_nul(k) or contains_nul(v) for k, v in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(contains_nul(item) for item in value)
    return False


class ApiModel(BaseModel):
    """`BaseModel` + the NUL-rejection contract (see module docstring)."""

    @model_validator(mode="before")
    @classmethod
    def _reject_nul_bytes(cls, data: Any) -> Any:
        if contains_nul(data):
            raise ValueError("NUL (\\x00) characters are not allowed")
        return data
