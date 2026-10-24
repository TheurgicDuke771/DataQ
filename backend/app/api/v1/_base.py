"""Shared request/response model base for the v1 API."""

from __future__ import annotations

from typing import Annotated, Any, Final

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from backend.app.db.models import TAG_NAME_MAX_LENGTH

# The one paging-total header name for every list endpoint that carries one (#925 introduced it on
# `/assets`; #1108 spread it to `/pipeline_runs`, `/incidents`, and `/runs` rather than each
# endpoint inventing its own convention).
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


class ApiRequestModel(ApiModel):
    """`ApiModel` for request bodies: an unknown field is a 422 naming it, not a
    silently-dropped no-op (#1505 — the sibling of `Settings`' own `extra='forbid'`,
    #209). Response models stay on `ApiModel` — `from_attributes` construction never
    applies `extra`, but a handful build a response via `model_validate(dict)`, where
    a surplus key would 500 instead of validating.

    A model used to shape BOTH directions (e.g. a suite's export document, also
    accepted back on import) is not a request model here — split into a
    request-only variant instead of subclassing this for a response.
    """

    model_config = ConfigDict(extra="forbid")


def updated_by_email(db: Any, row: Any) -> str | None:
    """Email of the admin a singleton-settings row records as `updated_by`, tolerating
    a SET NULL'd FK."""
    from backend.app.db.models import User

    if row is None or row.updated_by is None:
        return None
    user = db.get(User, row.updated_by)
    return user.email if user is not None else None


def _tag_names(value: Any) -> Any:
    """ORM ``Tag`` rows (what `from_attributes` hands a read model) → their names; plain strings
    pass through, so a response built from a dict validates too."""
    if isinstance(value, (list, tuple)):
        return [getattr(item, "name", item) for item in value]
    return value


#: `tags` on a read model (#888): the entity's tag names, sorted, from its ORM relationship.
TagNames = Annotated[list[str], BeforeValidator(_tag_names)]


#: Tag names on a write (#888): free text, normalized by the service (lower-case, trimmed,
#: single-spaced; a name nobody has used yet is created). The whole list replaces the entity's tags.
TagNamesIn = list[Annotated[str, Field(min_length=1, max_length=TAG_NAME_MAX_LENGTH)]]

TAGS_WRITE_DESCRIPTION = (
    "Replaces the entity's tags. Names are normalized (lower-case, trimmed) and a new name "
    "creates the tag; `[]` clears them. Omit to leave the tags as they are."
)
TAG_FILTER_DESCRIPTION = (
    "Only rows carrying this tag. Repeat the parameter to require every named tag (AND): "
    "`?tag=finance&tag=daily` returns rows tagged both. Matching ignores case. A name no tag "
    "has matches nothing, so an empty result is 'nothing carries all of these', not an error."
)
