"""JSON flat files (#1677): JSON Lines, or a top-level array of flat objects.

One parser types both shapes — `pyarrow.json` — so a column reads the same whether
the producer wrote NDJSON or an array. An array is re-presented as JSON Lines by
`_ArrayAsLines` rather than parsed by a second library with its own inference.

Two of pyarrow's inference choices are overridden, both at the reader so every
path (checks, monitors, profiler, schema drift, comparison) sees the same frame:

- **Strings stay strings.** pyarrow guesses ``timestamp[s]`` for an ISO string with
  whole seconds and leaves one with fractional seconds as text, so a column's type
  would flip on data alone (a spurious schema-drift alert) and a date would read
  back as midnight. JSON has no timestamp type; DataQ reads it as text, as the CSV
  reader does, and freshness parses it.
- **Nested values are refused**, naming the columns. An object or array inside a
  row has no honest single-column type for a check to run against.
"""

from __future__ import annotations

import codecs
import io
import json
import re
from collections.abc import Callable, Iterator
from typing import Any, BinaryIO

from backend.app.core.errors import SafeMonitorError

#: Bytes per parse block. Also the window a STREAMED read infers its column types
#: from, so it is pinned rather than left to pyarrow's default.
BLOCK_BYTES = 1 << 20

#: Bytes read per step while scanning or transcoding.
_READ_BYTES = 1 << 20

_NON_WS = re.compile(r"[^ \t\n\r]")
_BOM = codecs.BOM_UTF8

LINES = "lines"
ARRAY = "array"
EMPTY = "empty"


class JsonFileError(SafeMonitorError, ValueError):
    """A JSON file DataQ will not read — the message is DataQ's, never a value from the file."""


RawOpener = Callable[[], BinaryIO]


# ───────────────────────────── shape ─────────────────────────────


def detect_shape(raw: BinaryIO) -> str:
    """`LINES`, `ARRAY` or `EMPTY` from the first non-whitespace byte; rewinds ``raw``."""
    first = b""
    at_start = True
    while True:
        chunk = raw.read(_READ_BYTES)
        if not chunk:
            break
        if at_start and chunk.startswith(_BOM):
            chunk = chunk[len(_BOM) :]
        at_start = False
        stripped = chunk.lstrip(b" \t\r\n")
        if stripped:
            first = stripped[:1]
            break
    raw.seek(0)
    if not first:
        return EMPTY
    if first == b"{":
        return LINES
    if first == b"[":
        return ARRAY
    raise JsonFileError(
        "the file is neither JSON Lines (one object per line) nor a JSON array of objects"
    )


class _ArrayAsLines(io.RawIOBase):
    """A top-level JSON array of objects, read as JSON Lines.

    Each element is located with the stdlib decoder and its ORIGINAL text is
    emitted on one line — raw newlines can only sit between tokens (JSON escapes
    them inside strings), so folding them to spaces changes no value. pyarrow then
    parses the lines, so both shapes are typed by the same inference.
    """

    _OPEN, _FIRST, _ELEMENT, _SEPARATOR, _TRAILER, _DONE = range(6)

    def __init__(self, source: BinaryIO) -> None:
        self._source = source
        self._decoder = codecs.getincrementaldecoder("utf-8-sig")()
        self._json = json.JSONDecoder()
        self._text = ""
        self._pos = 0
        self._eof = False
        self._state = self._OPEN
        self._out = bytearray()
        self._elements = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        while len(self._out) < len(buffer) and self._state != self._DONE:
            self._step()
        taken = min(len(buffer), len(self._out))
        buffer[:taken] = self._out[:taken]
        del self._out[:taken]
        return taken

    def _more(self) -> bool:
        """Append the next decoded chunk, dropping what is already consumed."""
        if self._eof:
            return False
        chunk = self._source.read(_READ_BYTES)
        try:
            decoded = self._decoder.decode(chunk, final=not chunk)
        except UnicodeDecodeError:
            raise JsonFileError("the file is not UTF-8 text") from None
        self._text = self._text[self._pos :] + decoded
        self._pos = 0
        if not chunk:
            self._eof = True
        return True

    def _peek(self) -> str | None:
        """The next non-whitespace character (not consumed), or ``None`` at EOF."""
        while True:
            match = _NON_WS.search(self._text, self._pos)
            if match is not None:
                self._pos = match.start()
                return self._text[self._pos]
            self._pos = len(self._text)
            if not self._more():
                return None

    def _step(self) -> None:
        char = self._peek()
        if self._state == self._OPEN:
            if char != "[":
                raise JsonFileError("the file is not a JSON array")
            self._pos += 1
            self._state = self._FIRST
        elif self._state == self._FIRST:
            if char == "]":
                self._pos += 1
                self._state = self._TRAILER
            else:
                self._state = self._ELEMENT
        elif self._state == self._ELEMENT:
            self._element(char)
        elif self._state == self._SEPARATOR:
            if char == ",":
                self._pos += 1
                self._state = self._ELEMENT
            elif char == "]":
                self._pos += 1
                self._state = self._TRAILER
            else:
                raise self._unterminated(char)
        else:  # _TRAILER
            if char is not None:
                raise JsonFileError("the file has content after its top-level JSON array")
            self._state = self._DONE

    def _element(self, char: str | None) -> None:
        if char is None:
            raise self._unterminated(char)
        if char != "{":
            raise JsonFileError(
                f"element {self._elements + 1} of the JSON array is not an object — "
                "DataQ reads an array only when every element is a flat object (one row)"
            )
        while True:
            try:
                _, end = self._json.raw_decode(self._text, self._pos)
                break
            except json.JSONDecodeError:
                # Incomplete in this buffer, or malformed: only EOF tells them apart.
                if not self._more():
                    raise JsonFileError(
                        f"element {self._elements + 1} of the JSON array is not valid JSON"
                    ) from None
        span = self._text[self._pos : end]
        self._out += span.replace("\n", " ").replace("\r", " ").encode("utf-8")
        self._out += b"\n"
        self._pos = end
        self._elements += 1
        self._state = self._SEPARATOR

    def _unterminated(self, char: str | None) -> JsonFileError:
        if char is None:
            return JsonFileError("the JSON array is not terminated")
        return JsonFileError(
            f"expected ',' or ']' after element {self._elements} of the JSON array"
        )


def lines_opener(open_raw: RawOpener) -> Callable[[], BinaryIO | None]:
    """An opener of the file as JSON Lines — ``None`` for a file with no content at all."""

    def open_lines() -> BinaryIO | None:
        raw = open_raw()
        shape = detect_shape(raw)
        if shape == EMPTY:
            return None
        if shape == ARRAY:
            return io.BufferedReader(_ArrayAsLines(raw), buffer_size=_READ_BYTES)
        return raw

    return open_lines


# ───────────────────────────── parse ─────────────────────────────


_CHANGED = re.compile(r"Column\(([^)]*)\) changed from (\w+) to (\w+)")
_TWICE = re.compile(r"Column\(([^)]*)\) was specified twice")


def _column(path: str) -> str:
    return path.lstrip("/") or "(top level)"


def _classified(exc: Exception, *, streamed: bool) -> JsonFileError:
    """pyarrow's message, re-worded — it can quote a VALUE from the file ("couldn't parse:…")."""
    message = str(exc)
    if (match := _CHANGED.search(message)) is not None:
        column, before, after = match.groups()
        if column in ("", "/"):
            return JsonFileError("every line of a JSON Lines file must be one object")
        if before == "null" and streamed:
            return _streamed_drift()
        return JsonFileError(
            f"column {_column(column)!r} holds {before} values and later {after} values — "
            "a JSON column must keep one type (null aside)"
        )
    if (match := _TWICE.search(message)) is not None:
        return JsonFileError(f"an object repeats the key {_column(match.group(1))!r}")
    if streamed and ("unexpected field" in message or "Failed to convert JSON" in message):
        return _streamed_drift()
    if "Empty JSON" in message:
        return JsonFileError("the file holds no JSON objects")
    return JsonFileError(
        "the file is not valid JSON Lines (one object per line) or a JSON array of objects"
    )


def _streamed_drift() -> JsonFileError:
    return JsonFileError(
        f"the column types inferred from the file's first {BLOCK_BYTES // (1 << 20)} MiB do not "
        "hold for the rest of it (a field that first appears later, a null-only column that "
        "later holds values, or an integer column that later holds decimals). A sampled read "
        "types a JSON file from its first block; turn sampling off to type the whole file, "
        "or make the early rows representative"
    )


def _read_options(*, threads: bool = True) -> Any:
    import pyarrow.json as pj

    return pj.ReadOptions(block_size=BLOCK_BYTES, use_threads=threads)


def _parse_options(text_columns: dict[str, Any] | None = None) -> Any:
    import pyarrow as pa
    import pyarrow.json as pj

    if not text_columns:
        return pj.ParseOptions()
    return pj.ParseOptions(explicit_schema=pa.schema(list(text_columns.items())))


def _text_columns(schema: Any) -> dict[str, Any]:
    """Columns pyarrow typed as temporal — every value was a string, so read them as one."""
    import pyarrow as pa

    return {
        field.name: pa.string()
        for field in schema
        if pa.types.is_timestamp(field.type) or pa.types.is_date(field.type)
    }


def refuse_nested(schema: Any) -> None:
    """Refuse a schema carrying an object or array value, naming the columns."""
    import pyarrow as pa

    nested = [
        field.name
        for field in schema
        if pa.types.is_nested(field.type) or pa.types.is_dictionary(field.type)
    ]
    if nested:
        shown = ", ".join(repr(name) for name in nested[:10])
        more = f" and {len(nested) - 10} more" if len(nested) > 10 else ""
        raise JsonFileError(
            f"column(s) {shown}{more} hold nested objects or arrays — DataQ checks flat JSON "
            "only; flatten them upstream (one scalar per column)"
        )


def _is_empty(exc: Exception) -> bool:
    return "Empty JSON" in str(exc)


def _empty_table() -> Any:
    import pyarrow as pa

    return pa.table({})


def read_table(open_raw: RawOpener) -> Any:
    """The whole file as an Arrow table, typed across every row (not just the first block)."""
    import pyarrow as pa
    import pyarrow.json as pj

    open_lines = lines_opener(open_raw)

    def parse(text_columns: dict[str, Any]) -> Any:
        stream = open_lines()
        if stream is None:
            return None
        try:
            return pj.read_json(
                stream, read_options=_read_options(), parse_options=_parse_options(text_columns)
            )
        except pa.ArrowInvalid as exc:
            if _is_empty(exc):
                return None
            raise _classified(exc, streamed=False) from None

    first = first_block_schema(open_raw)
    if first is None:
        return _empty_table()
    refuse_nested(first)
    text = _text_columns(first)
    table = parse(text)
    if table is None:
        return _empty_table()
    late = _text_columns(table.schema)
    if late:
        # A field typed temporal only past the first block: parse again with it as text.
        del table
        table = parse({**text, **late})
        if table is None:  # pragma: no cover — the first parse just returned rows
            return _empty_table()
    refuse_nested(table.schema)
    return table.select(_file_order(first, table.schema))


def _file_order(first: Any, schema: Any) -> list[str]:
    """Columns in the order the file introduces them.

    pyarrow puts explicitly-typed columns FIRST, so the text override alone would
    reorder a file's columns — and the order is what the profiler lists and a
    positional reader relies on.
    """
    seen = [name for name in first.names if name in schema.names]
    return seen + [name for name in schema.names if name not in set(seen)]


def _first_block(stream: BinaryIO) -> bytes:
    """The bytes pyarrow's first parse block holds: `BLOCK_BYTES`, cut back to the last
    newline (or on to the first one, for a line longer than a block).

    Read here rather than by pyarrow, whose streaming reader reads ~32 blocks ahead the
    moment it opens (measured: 34 MiB of a 50 MiB object) — for a schema peek that is
    thirty-odd range requests of data nobody looks at.
    """
    head = bytearray(stream.read(BLOCK_BYTES))
    if len(head) < BLOCK_BYTES:
        return bytes(head)
    cut = head.rfind(b"\n")
    while cut < 0:
        more = stream.read(BLOCK_BYTES)
        if not more:
            return bytes(head)
        found = more.find(b"\n")
        if found >= 0:
            cut = len(head) + found
        head += more
    return bytes(head[: cut + 1])


def first_block_schema(open_raw: RawOpener) -> Any | None:
    """The schema pyarrow infers from the first block — ``None`` for a file with no objects."""
    import pyarrow as pa
    import pyarrow.json as pj

    stream = lines_opener(open_raw)()
    if stream is None:
        return None
    try:
        reader = pj.open_json(io.BytesIO(_first_block(stream)), read_options=_read_options())
    except pa.ArrowInvalid as exc:
        if _is_empty(exc):
            return None
        raise _classified(exc, streamed=False) from None
    try:
        schema = reader.schema
    finally:
        reader.close()
    return schema


def stream_schema(open_raw: RawOpener) -> Any:
    """The schema a streamed read gives the file: its first block, temporal guesses read
    as text, nested columns refused. An empty file has no columns.
    """
    import pyarrow as pa

    first = first_block_schema(open_raw)
    if first is None:
        return pa.schema([])
    refuse_nested(first)
    text = _text_columns(first)
    return pa.schema([pa.field(f.name, text.get(f.name, f.type)) for f in first])


def open_batches(open_raw: RawOpener) -> tuple[Iterator[Any], Any, Callable[[], None]]:
    """A forward stream of record batches, typed from the first block (#595 sampling).

    The schema is fixed after the first block, so a later row that does not fit it
    is refused (`_streamed_drift`) rather than retyped mid-stream.
    """
    import pyarrow as pa
    import pyarrow.json as pj

    first = first_block_schema(open_raw)
    if first is None:
        empty = pa.schema([])
        return iter(()), empty, lambda: None
    refuse_nested(first)
    stream = lines_opener(open_raw)()
    if stream is None:  # pragma: no cover — the file had objects a moment ago
        return iter(()), pa.schema([]), lambda: None
    try:
        reader = pj.open_json(
            stream,
            read_options=_read_options(),
            parse_options=_parse_options(_text_columns(first)),
        )
    except pa.ArrowInvalid as exc:
        raise _classified(exc, streamed=True) from None
    order = _file_order(first, reader.schema)

    def batches() -> Iterator[Any]:
        try:
            for batch in reader:
                yield batch.select(order)
        except pa.ArrowInvalid as exc:
            raise _classified(exc, streamed=True) from None

    return batches(), pa.schema([reader.schema.field(name) for name in order]), reader.close


def count_rows(open_raw: RawOpener) -> int:
    """Objects in the file — every field ignored, so no type can make the count fail."""
    import pyarrow as pa
    import pyarrow.json as pj

    stream = lines_opener(open_raw)()
    if stream is None:
        return 0
    options = pj.ParseOptions(explicit_schema=pa.schema([]), unexpected_field_behavior="ignore")
    try:
        reader = pj.open_json(stream, read_options=_read_options(), parse_options=options)
        with reader:
            return sum(batch.num_rows for batch in reader)
    except pa.ArrowInvalid as exc:
        if _is_empty(exc):
            return 0
        raise _classified(exc, streamed=False) from None


def to_frame(table: Any) -> Any:
    """Arrow-backed pandas, as the Parquet reader produces — no numpy copy of the buffers."""
    import pandas as pd

    return table.to_pandas(types_mapper=pd.ArrowDtype)
