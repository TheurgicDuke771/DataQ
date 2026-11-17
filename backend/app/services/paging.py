"""Opaque cursors for newest-first lists (#1987).

``limit``/``offset`` makes the database walk and discard every skipped row, so a deep page costs
in proportion to its offset. A cursor instead names the last row a client saw, by the list's
total order (a timestamp, then the row's id), and the next page starts after it with one index
descent whatever the depth.

A cursor is not a secret and carries nothing but a position: the caller's visibility filters
still apply to every page, so a forged cursor can only land somewhere in rows the caller may
already read. It is bound to the list it came from (``kind``), so one list's cursor is refused by
another, and it is versioned so the format can change.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, tuple_
from sqlalchemy.orm import InstrumentedAttribute

from backend.app.core.errors import DataQError

CURSOR_VERSION = 1
#: A position in a list: the sort timestamp and the row id (the tie-break), as the list orders.
Position = tuple[datetime, uuid.UUID]


class InvalidCursorError(DataQError):
    code = "invalid_cursor"
    status_code = 422

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"invalid cursor: {reason}",
            detail={"hint": "use the X-Next-Cursor value of a response from the same list"},
        )


class CursorWithOffsetError(DataQError):
    code = "cursor_with_offset"
    status_code = 422

    def __init__(self) -> None:
        super().__init__(
            "send either `cursor` or `offset`, not both",
            detail={"hint": "a cursor already says where the page starts"},
        )


def encode_cursor(kind: str, moment: datetime, row_id: uuid.UUID) -> str:
    """The cursor for the page that starts after the row at (``moment``, ``row_id``)."""
    payload = {
        "v": CURSOR_VERSION,
        "k": kind,
        "t": moment.astimezone(UTC).isoformat(),
        "i": str(row_id),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def decode_cursor(kind: str, token: str) -> Position:
    """The position a cursor names. Raises :class:`InvalidCursorError` for anything that is not a
    well-formed, current-version cursor of this list."""
    try:
        padded = token + "=" * (-len(token) % 4)
        payload: Any = json.loads(base64.urlsafe_b64decode(padded.encode()))
        if not isinstance(payload, dict) or payload.get("v") != CURSOR_VERSION:
            raise InvalidCursorError("not a cursor of this version")
        if payload.get("k") != kind:
            raise InvalidCursorError("it belongs to a different list")
        moment = datetime.fromisoformat(str(payload["t"]))
        if moment.tzinfo is None:
            raise InvalidCursorError("its timestamp has no time zone")
        return moment, uuid.UUID(str(payload["i"]))
    except InvalidCursorError:
        raise
    except (ValueError, KeyError, TypeError, binascii.Error, UnicodeDecodeError, RecursionError):
        raise InvalidCursorError("it is not readable") from None


def after(
    moment: InstrumentedAttribute[datetime],
    row_id: InstrumentedAttribute[uuid.UUID],
    position: Position,
) -> ColumnElement[bool]:
    """The rows that come after ``position`` in a newest-first (both columns descending) list:
    ``(moment, id) < position``, a row-value comparison one descent of the list's index serves."""
    return tuple_(moment, row_id) < (position[0], position[1])
