"""Shared request/response model base for the v1 API."""

from __future__ import annotations

from typing import Any, Final

from pydantic import BaseModel, model_validator

# The one paging-total header name for every list endpoint that carries one (#925 introduced it on
# `/assets`; #1108 spread it to `/pipeline_runs`, `/incidents`.
TOTAL_COUNT_HEADER: Final = "X-Total-Count"


def total_count_responses(description: str) -> dict[int | str, dict[str, Any]]:
    """The OpenAPI `responses=` fragment documenting `TOTAL_COUNT_HEADER` on a
    200, shared verbatim by every list endpoint that sets it — one Swagger
    shape instead of four independently-worded copies.
    """
    return {
        200: {
            "headers": {
                TOTAL_COUNT_HEADER: {"description": description, "schema": {"type": "integer"}}
            }
        }
    }


def contains_nul(value: Any) -> bool:
    """True if a NUL (``\\x00``) appears in any string within ``value``
    (recursing through dict keys/values and list/tuple/set items).
    """
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
