"""Flat-file (ADLS Gen2 / S3) IO + GX `CheckRunner`.

Two responsibilities for the flat-file datasources, both behind the same
primitives the SQL adapters use (raw config dict + resolved secret, never the ORM
row — keeps `datasources/` decoupled from `db/`):

* **IO** — `download_bytes` fetches a whole object/blob and `read_dataframe`
  parses it into pandas; `RangeReader` + `read_range` serve the cases that need
  only *part* of it. Shared by the column profiler (service layer) and the runner.

  Which one a caller reaches for is a real decision, not a preference: running
  checks needs every row and column, but a schema read or a row count does not,
  and paying full egress plus worker memory for either on a scheduled path is the
  #854 shape (#882/#942). Parquet answers both from its footer — a couple of
  small range GETs whatever the object's size — and a CSV takes a bounded head
  read for its schema and a streamed scan for its count.
* **Runner** — `FlatFileCheckRunner` runs GX expectations against the file by
  loading it into an in-memory pandas DataFrame and handing that to GX's pandas
  datasource, then mapping the result via the shared `gx_runner` machinery. The
  `CheckRunner` interface is table-shaped; for a flat-file datasource the
  ``table`` argument carries the **file path** and ``schema`` is unused.

GX runs entirely in-process on the DataFrame, so — unlike the warehouse runners —
the run path is fully testable with a canned frame; only the network download is
the deferred-smoke seam.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Generator, Iterable, Iterator
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar

import great_expectations as gx

from backend.app.core.logging import get_logger
from backend.app.core.s3_endpoint import addressing_config_kwargs
from backend.app.core.secrets import SecretStore
from backend.app.datasources.adls import AdlsConfig
from backend.app.datasources.base import CheckOutcome, CheckSpec, MonitorSpec, SuiteOutcome
from backend.app.datasources.gx_runner import run_expectations
from backend.app.datasources.monitors import (
    FRESHNESS,
    VOLUME,
    MonitorConfigError,
    SafeMonitorError,
    freshness_column,
    run_monitor_specs,
)
from backend.app.datasources.s3 import S3Config

# Connector timeouts (seconds): fail fast rather than hang the worker thread.
# _READ_TIMEOUT is deliberately longer than the SQL profiler's network timeout
# (profile_service._NETWORK_TIMEOUT = 30): it covers a full-object download (the
# whole CSV/Parquet is pulled before parsing), not a single warehouse query, so a
# large file legitimately needs more headroom. Not accidental drift (#147).
_CONNECT_TIMEOUT = 10
_READ_TIMEOUT = 60

log = get_logger(__name__)

_FILE_TYPES = {"adls_gen2", "s3"}

# Sort floor for files the store reports without a modified time.
_MIN_DT = datetime.min.replace(tzinfo=UTC)

# Delimiters `sniff_delimiter` will consider, and the fallback when it can't tell
# (which is also the pre-#476 behaviour, so a failed sniff never regresses a file
# that parses today). Deliberately a short allowlist rather than letting
# `csv.Sniffer` pick freely: given a header like `name,title` it will happily
# nominate `e` or a space as the delimiter, which is a *worse* silent wrong
# answer than assuming a comma.
_CSV_DELIMITERS = ",;\t|"
_DEFAULT_DELIMITER = ","

# How much of the object to hand the sniffer. The header plus a few rows is
# plenty and keeps the decode bounded on a large file.
_SNIFF_BYTES = 64 * 1024

#: Window size for a reader that walks an object sequentially (the CSV row
#: count). Bounded memory is the goal, not a tiny footprint — one buffer of this
#: size beats both a full download and a storm of small requests.
_STREAM_CHUNK = 8 * 1024 * 1024

#: Head bytes fetched to type/name a CSV's columns. Comfortably covers a header
#: plus the type sample on any realistic row width, and is what keeps schema
#: introspection off the full-object path (#882).
_CSV_HEAD_BYTES = 1024 * 1024


def format_from_path(path: str) -> str | None:
    """Infer the file format from the path extension (`None` if unrecognised)."""
    lower = path.lower()
    if lower.endswith(".csv"):
        return "csv"
    if lower.endswith((".parquet", ".pq")):
        return "parquet"
    return None


def sniff_delimiter(sample: bytes) -> str:
    """Guess a CSV's delimiter from its leading bytes, falling back to a comma.

    Pure (no IO) so the decision is testable without a datasource. Sniffing is
    per-*file* on purpose: a flat-file connection is a whole bucket/container and
    the files under it need not agree on a delimiter, so a per-connection hint
    would be the wrong granularity.

    Never raises — an undecidable sample (single column, empty file, binary junk)
    yields `_DEFAULT_DELIMITER`.
    """
    text = sample.decode("utf-8", errors="replace")
    # Sniff over whole lines only: a sample cut mid-row can end in a fragment
    # whose field counts don't line up, which is exactly what Sniffer keys off.
    head, newline, _ = text.rpartition("\n")
    if newline:
        text = head
    if not text.strip():
        return _DEFAULT_DELIMITER
    try:
        return csv.Sniffer().sniff(text, delimiters=_CSV_DELIMITERS).delimiter
    except csv.Error:
        return _DEFAULT_DELIMITER


def read_csv_bytes(raw: io.BytesIO, **kwargs: Any) -> Any:
    """`pd.read_csv` over `raw` with the delimiter sniffed from its header (#476).

    The single CSV-parsing seam for every flat-file path — runner, profiler,
    column lister, schema-drift introspection — so they can never disagree about
    what a file's columns are. Extra `kwargs` (``nrows``, ``usecols``, …) pass
    straight through; the buffer is rewound before parsing, so callers may hand
    over a buffer at any position.
    """
    import pandas as pd

    # `read`, not `getvalue()[:n]`: getvalue copies the ENTIRE buffer before the
    # slice, so on a large CSV — which the runner already holds whole in memory —
    # picking a delimiter would transiently double peak RSS. The bound is meant to
    # cap the work, not just the decode.
    raw.seek(0)
    sep = sniff_delimiter(raw.read(_SNIFF_BYTES))
    raw.seek(0)
    return pd.read_csv(raw, sep=sep, **kwargs)


def _s3_client(cfg: S3Config, secret: str) -> Any:
    """A boto3 S3 client for `cfg` with the standard fail-fast timeouts.

    The one client every S3 read path is built on — `download_bytes`,
    `object_size`, `read_range`, `iter_files` and the schema/count reads — so the
    S3-compatible endpoint (#1063) reaches all of them from here.
    """
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        region_name=cfg.region,
        aws_access_key_id=cfg.access_key_id,
        aws_secret_access_key=secret,
        endpoint_url=cfg.endpoint_url,
        config=Config(
            connect_timeout=_CONNECT_TIMEOUT,
            read_timeout=_READ_TIMEOUT,
            **addressing_config_kwargs(cfg.endpoint_url, cfg.addressing_style),
        ),
    )


def _blob_service(acfg: AdlsConfig, secret: str) -> Any:
    """An ADLS `BlobServiceClient` for `acfg` (caller must `.close()` it)."""
    from azure.storage.blob import BlobServiceClient

    return BlobServiceClient(account_url=acfg.account_url, credential=secret)


def download_bytes(*, conn_type: str, config: dict[str, Any], path: str, secret: str) -> bytes:
    """Fetch the object/blob bytes from S3 or ADLS Gen2 (live seam).

    Takes the connection's `type` + raw `config` + resolved `secret` (the caller
    owns the SecretStore), so this module never touches the DB.
    """
    if conn_type == "s3":
        cfg = S3Config.model_validate(config)
        body: bytes = _s3_client(cfg, secret).get_object(Bucket=cfg.bucket, Key=path)["Body"].read()
        return body

    acfg = AdlsConfig.model_validate(config)
    client_az = _blob_service(acfg, secret)
    try:
        blob = client_az.get_blob_client(container=acfg.container, blob=path)
        downloaded: bytes = blob.download_blob().readall()
        return downloaded
    finally:
        client_az.close()


def object_size(*, conn_type: str, config: dict[str, Any], path: str, secret: str) -> int:
    """Byte length of exactly ``path`` — one metadata call (live seam, #882)."""
    if conn_type == "s3":
        cfg = S3Config.model_validate(config)
        length: int = _s3_client(cfg, secret).head_object(Bucket=cfg.bucket, Key=path)[
            "ContentLength"
        ]
        return length

    acfg = AdlsConfig.model_validate(config)
    client_az = _blob_service(acfg, secret)
    try:
        blob = client_az.get_blob_client(container=acfg.container, blob=path)
        size: int = blob.get_blob_properties().size
        return size
    finally:
        client_az.close()


def read_range(
    *, conn_type: str, config: dict[str, Any], path: str, secret: str, start: int, length: int
) -> bytes:
    """``length`` bytes of ``path`` from offset ``start`` (live seam, #882).

    Both stores serve HTTP range requests; this is the primitive that lets a
    Parquet footer or a CSV header be read without paying for the whole object.

    A range that *starts* in bounds and *ends* past EOF is served short by both
    stores, so asking for more than exists is safe. A range whose **start** is at
    or past EOF is not: S3 answers 416 and Azure raises `InvalidRange`. That is
    reachable — `RangeReader` snapshots the size once, so an object truncated by a
    producer mid-read can push a later request past the new end. It surfaces as a
    classified read failure at every call site rather than a wrong answer, which
    is the right outcome; it is called out here so nobody plans around a
    short-read that will not happen.
    """
    if length <= 0:
        return b""
    if conn_type == "s3":
        cfg = S3Config.model_validate(config)
        # Inclusive end, per RFC 7233 — `bytes=0-1023` is the first 1024 bytes.
        body: bytes = (
            _s3_client(cfg, secret)
            .get_object(Bucket=cfg.bucket, Key=path, Range=f"bytes={start}-{start + length - 1}")[
                "Body"
            ]
            .read()
        )
        return body

    acfg = AdlsConfig.model_validate(config)
    client_az = _blob_service(acfg, secret)
    try:
        blob = client_az.get_blob_client(container=acfg.container, blob=path)
        downloaded: bytes = blob.download_blob(offset=start, length=length).readall()
        return downloaded
    finally:
        client_az.close()


class RangeReader(io.RawIOBase):
    """A seekable file over an object store, backed by range GETs (#882/#942).

    Hand this to ``pyarrow`` instead of a fully-downloaded ``BytesIO`` and a
    Parquet reader takes the two or three small reads it needs to land on the
    footer, rather than the multi-GB object. That is the entire point: a
    schema-only monitor or a row count used to pay full egress **and** worker
    memory on every scheduled run — the #854 unbounded-read-on-a-scheduled-path
    shape, in the one place a file format makes it avoidable.

    Reads are coalesced into ``_CHUNK``-sized windows and the last window is
    kept, so a reader taking a footer apart in small sequential reads issues one
    request, not dozens. Deliberately a *single* cached window rather than a full
    block cache: Parquet metadata reads are two localized bursts (the tail, then
    the footer), which one window already serves, and an unbounded cache would
    quietly reintroduce the memory cost this class exists to remove.
    """

    #: Default minimum bytes per request. Sized for the *seeking* access pattern
    #: (a Parquet footer: land near the end, read a few structures) where a big
    #: window is wasted egress. A **sequential** reader should pass a much larger
    #: `chunk`: streaming a 2 GB CSV at this size would be ~8,000 round trips, and
    #: trading one download for thousands of requests is not an improvement.
    _CHUNK = 256 * 1024

    def __init__(
        self,
        *,
        conn_type: str,
        config: dict[str, Any],
        path: str,
        secret: str,
        chunk: int | None = None,
    ) -> None:
        self._conn_type = conn_type
        self._config = config
        self._path = path
        self._secret = secret
        self._chunk = chunk or self._CHUNK
        self._size = object_size(conn_type=conn_type, config=config, path=path, secret=secret)
        self._pos = 0
        self._window = b""
        self._window_start = 0
        #: Range requests actually issued — asserted by tests, so "it took the
        #: cheap path" is verifiable rather than assumed.
        self.requests = 0

    # ── io.RawIOBase surface pyarrow/pandas need ──────────────────────────
    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            self._pos = offset
        elif whence == io.SEEK_CUR:
            self._pos += offset
        elif whence == io.SEEK_END:
            self._pos = self._size + offset
        else:  # pragma: no cover — io module defines only the three
            raise ValueError(f"invalid whence {whence!r}")
        self._pos = max(0, min(self._pos, self._size))
        return self._pos

    def readinto(self, buffer: Any) -> int:
        data = self.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)

    def read(self, size: int = -1) -> bytes:
        remaining = self._size - self._pos
        want = remaining if size is None or size < 0 else min(size, remaining)
        if want <= 0:
            return b""
        if not self._covers(self._pos, want):
            self._fetch(self._pos, want)
        offset = self._pos - self._window_start
        self._pos += want
        return self._window[offset : offset + want]

    def _covers(self, start: int, length: int) -> bool:
        return (
            bool(self._window)
            and start >= self._window_start
            and start + length <= self._window_start + len(self._window)
        )

    def _fetch(self, start: int, length: int) -> None:
        span = max(length, self._chunk)
        # Clamp so a read near the end doesn't ask for bytes past EOF, and so a
        # read of exactly the tail still gets a full window where one exists.
        span = min(span, self._size - start)
        self._window = read_range(
            conn_type=self._conn_type,
            config=self._config,
            path=self._path,
            secret=self._secret,
            start=start,
            length=span,
        )
        self._window_start = start
        self.requests += 1


def parquet_row_count(*, conn_type: str, config: dict[str, Any], path: str, secret: str) -> int:
    """Row count from a Parquet file's own footer — no data read (#942).

    Parquet records ``num_rows`` in its metadata, so counting rows is a metadata
    lookup, not a scan. Reached through `RangeReader`, this costs a couple of
    small range GETs regardless of whether the object is 2 MB or 2 GB.
    """
    import pyarrow.parquet as pq

    reader = RangeReader(conn_type=conn_type, config=config, path=path, secret=secret)
    rows: int = pq.ParquetFile(reader).metadata.num_rows
    return rows


def csv_row_count(*, conn_type: str, config: dict[str, Any], path: str, secret: str) -> int:
    """Row count of a CSV, streamed in batches — never a full DataFrame (#942).

    A CSV has no footer, so the rows must genuinely be scanned. This uses
    pyarrow's incremental reader rather than counting newlines: **a newline count
    is not a row count** — a quoted field may legally contain one — and swapping
    an exact answer for a plausible wrong one is precisely the trade this
    codebase keeps paying for. Batches are counted and discarded, so peak memory
    is one batch rather than the whole file.
    """
    import pyarrow.csv as pv

    # A big window: this reader walks the object end to end, and at the seeking
    # default a multi-GB CSV would become thousands of range requests.
    reader = RangeReader(
        conn_type=conn_type, config=config, path=path, secret=secret, chunk=_STREAM_CHUNK
    )
    sep = sniff_delimiter(
        read_range(
            conn_type=conn_type,
            config=config,
            path=path,
            secret=secret,
            start=0,
            length=_SNIFF_BYTES,
        )
    )
    with pv.open_csv(reader, parse_options=pv.ParseOptions(delimiter=sep)) as batches:
        # The header is consumed by the reader, so batch rows are data rows.
        return sum(batch.num_rows for batch in batches)


def read_csv_head(
    *, conn_type: str, config: dict[str, Any], path: str, secret: str, rows: int
) -> Any:
    """Parse the first ``rows`` data rows of a CSV from a bounded head read (#882).

    The last line of a byte-range almost always lands mid-row, and a half-row
    parses as a *complete* row with empty trailing fields — which would silently
    change an inferred dtype (a truncated ``12345`` becomes ``123``; a cut string
    column reads as all-null). So the trailing partial line is discarded before
    parsing, unless the range covered the whole object.

    Returns a pandas DataFrame of at most ``rows`` rows; the caller reads names
    and dtypes off it.
    """
    # One byte MORE than the window, so a short read is unambiguous evidence the
    # object ended. Testing `len(head) == _CSV_HEAD_BYTES` instead would call a
    # file of exactly that size "truncated" and discard its genuinely-complete
    # last row — a complete read mistaken for a cut one.
    head = read_range(
        conn_type=conn_type,
        config=config,
        path=path,
        secret=secret,
        start=0,
        length=_CSV_HEAD_BYTES + 1,
    )
    if len(head) > _CSV_HEAD_BYTES:
        cut = head.rfind(b"\n")
        # No newline at all in a full window means one enormous line; there is no
        # complete row to keep, so hand the parser what we have rather than
        # nothing and let it fail honestly.
        head = head[:cut] if cut != -1 else head[:_CSV_HEAD_BYTES]
    return read_csv_bytes(io.BytesIO(head), nrows=rows)


def row_count(*, conn_type: str, config: dict[str, Any], path: str, secret: str) -> int:
    """Rows in a flat file, by the cheapest route the format allows (#942).

    Parquet answers from its footer; CSV streams. Neither materialises a pandas
    DataFrame, which is what a nightly volume monitor on a multi-GB object used
    to do on every run.
    """
    fmt = format_from_path(path)
    if fmt is None:
        raise ValueError(f"unsupported flat-file format for path {path!r}")
    if fmt == "csv":
        return csv_row_count(conn_type=conn_type, config=config, path=path, secret=secret)
    return parquet_row_count(conn_type=conn_type, config=config, path=path, secret=secret)


def read_dataframe(*, conn_type: str, config: dict[str, Any], path: str, secret: str) -> Any:
    """Download and parse the **whole** file into a pandas DataFrame (live seam).

    The runner reads the full file — every row and column — because a check may
    reference any column and row-count/null checks must be exact (unlike the
    profiler, which samples + projects). Raises `ValueError` for an unknown
    format.
    """
    import pandas as pd

    fmt = format_from_path(path)
    if fmt is None:
        raise ValueError(f"unsupported flat-file format for path {path!r}")
    raw = io.BytesIO(download_bytes(conn_type=conn_type, config=config, path=path, secret=secret))
    if fmt == "csv":
        return read_csv_bytes(raw)
    return pd.read_parquet(raw, dtype_backend="pyarrow")


class FlatFileReadError(SafeMonitorError, RuntimeError):
    """The object couldn't be downloaded/parsed — reason CLASSIFIED, never echoed.

    A monitor's error message is persisted to `results` and rendered in the UI,
    alerts and MCP output, so a raw object-store SDK exception must not reach it:
    Azure auth failures on this project have carried the SAS query string in their
    message (#828/#839). The exception type is logged (where the redactor sits) and
    only a classification travels outward.
    """


def _is_temporal(series: Any) -> bool:
    """Whether ``series`` already holds date/time values, numpy- **or** Arrow-backed.

    `pd.api.types.is_datetime64_any_dtype` alone is not enough: `read_dataframe`
    reads Parquet with ``dtype_backend="pyarrow"``, so a timestamp column arrives
    as ``timestamp[ns][pyarrow]``, for which that check returns **False**. Missing
    the Arrow case is what made column-based freshness fail on every Parquet file.
    """
    import pandas as pd

    if pd.api.types.is_datetime64_any_dtype(series):
        return True
    arrow_type = getattr(series.dtype, "pyarrow_dtype", None)
    if arrow_type is None:
        return False
    import pyarrow as pa

    return bool(pa.types.is_timestamp(arrow_type) or pa.types.is_date(arrow_type))


def max_timestamp(series: Any, *, column: str) -> Any:
    """The newest timestamp in ``series``, or ``None`` if it holds none (#520).

    Flat-file timestamps are not typed the way warehouse ones are: a Parquet
    column arrives as a real datetime, but the same column in a CSV arrives as
    **object-dtype strings**, whose ``max()`` is a string the age math rejects.
    So strings are parsed here.

    Numeric columns are **refused, not parsed**. ``pd.to_datetime`` cheerfully
    reads integers as epoch offsets, so pointing a freshness monitor at an id
    column would silently date it to 1970 and fire critical staleness forever —
    a confident wrong answer where "this isn't a timestamp" is the truth.

    A **mostly-unparseable** text column is refused for the same reason: with
    ``errors="coerce"``, 99 junk values and one real date yield a confident metric
    derived from that single row, which then bands as critically stale forever.
    Requiring a majority to parse means "you pointed at the wrong column" surfaces
    as an error instead of a plausible number. A minority of junk still parses
    (it behaves like NULLs, matching ``MAX`` in SQL).

    Caveat, documented rather than guessed at: for *ambiguous* text dates
    (``06/07/2026``) the parse follows pandas' day-first inference, so a
    non-ISO-8601 CSV can be read month-first. Use ISO-8601 or Parquet where the
    distinction matters; a heuristic of our own would just be a second guess.
    """
    import pandas as pd

    cleaned = series.dropna()
    if cleaned.empty:
        return None
    if _is_temporal(cleaned):  # numpy- or Arrow-backed; already an instant
        return cleaned.max()
    # Refuse on NUMERIC, rather than accept only object/string. The inverted form
    # excluded Arrow-backed timestamps (Parquet) along with the numerics, so
    # freshness told users their timestamp column was not a timestamp.
    if pd.api.types.is_numeric_dtype(cleaned) or pd.api.types.is_bool_dtype(cleaned):
        raise MonitorConfigError(  # see the epoch trap above
            f"freshness column {column!r} is {cleaned.dtype}, not a date/timestamp"
        )
    parsed = pd.to_datetime(cleaned, errors="coerce", utc=True).dropna()
    if len(parsed) * 2 < len(cleaned):
        raise MonitorConfigError(
            f"freshness column {column!r} is mostly not timestamps "
            f"({len(parsed)} of {len(cleaned)} values parsed) — check the column name"
        )
    return parsed.max()


def file_last_modified(
    *, conn_type: str, config: dict[str, Any], path: str, secret: str
) -> datetime | None:
    """The store's last-modified time for exactly ``path`` (live seam, #520).

    The arrival-time source for a column-less freshness monitor. A **single
    metadata call** (`head_object` / `get_blob_properties`) rather than a prefix
    listing: this runs on every scheduled monitor run, and both stores list by
    prefix, so a key like `data/orders.csv` sitting among dated siblings would
    drain the whole page set on each run — the unbounded-read-on-a-scheduled-path
    defect from #854. It is also exact by construction, which the listing version
    had to filter for (`orders.csv` is a prefix of `orders.csv.bak`).

    ``None`` when the object isn't there; the caller turns that into a per-check
    error rather than a silent pass, because a missing file is precisely the
    incident this monitor exists to catch. Any **other** failure (auth, network)
    propagates — that is this call's second job, as the store-reachability probe.
    """
    if conn_type == "s3":
        from botocore.exceptions import ClientError

        cfg = S3Config.model_validate(config)
        try:
            head = _s3_client(cfg, secret).head_object(Bucket=cfg.bucket, Key=path)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        modified: datetime | None = head.get("LastModified")
        return modified

    from azure.core.exceptions import ResourceNotFoundError

    acfg = AdlsConfig.model_validate(config)
    client_az = _blob_service(acfg, secret)
    try:
        blob = client_az.get_blob_client(container=acfg.container, blob=path)
        properties: datetime | None = blob.get_blob_properties().last_modified
        return properties
    except ResourceNotFoundError:
        return None
    finally:
        client_az.close()


class FlatFileCheckRunner:
    """`CheckRunner` for flat files — loads the file into pandas, runs GX on it.

    Holds the resolved credential (like `SnowflakeCheckRunner` holds its
    connection string), so `run_checks` is self-contained. ``table`` is the file
    path; ``schema`` is ignored (flat files have no schema namespace).
    """

    supported_monitor_kinds: ClassVar[frozenset[str]] = frozenset({FRESHNESS, VOLUME})

    def __init__(self, *, conn_type: str, config: dict[str, Any], secret: str) -> None:
        self._conn_type = conn_type
        self._config = config
        self._secret = secret

    def run_monitors(
        self, *, table: str, schema: str | None, monitors: list[MonitorSpec]
    ) -> list[CheckOutcome]:
        """Evaluate freshness/volume monitors on a flat file — no SQL (#520).

        Reuses the shared `monitors.run_monitor_specs` banding loop; only the
        scalar source differs:

        * **volume** — the resolved batch's row count.
        * **freshness with a ``column``** — ``MAX(column)`` over that frame, the
          same semantics as the SQL runners.
        * **freshness with no column** — the object's last-modified time, i.e.
          *when the file landed*. This is the case the SQL runners can't express
          and the reason #520 matters: on a landing zone, "the producer stopped
          sending files" is the incident, and an in-file MAX cannot see it (the
          newest file is old, but its rows look perfectly fresh).

        Arrival time is fetched **once, up front, for every run** — it is both the
        cheap freshness answer and the store-reachability probe, so a bad
        credential or unreachable container propagates and fails the whole run
        instead of erroring each monitor separately (the open-connection-first
        contract the SQL and Iceberg runners keep).

        The file itself is downloaded **lazily and at most once**, only if some
        monitor actually needs its contents — so an arrival-time-only check costs
        a listing, never a data read.

        **Volume does not need those contents** (#942). A row count comes from the
        Parquet footer, or from a streamed CSV scan — never from materialising the
        whole object as a DataFrame, which a nightly monitor on a multi-GB target
        used to do on every single run. The exception is a run that is *already*
        reading the frame for a column-freshness monitor: then the count comes off
        that frame, so the object is still read exactly once.
        """
        # The establishment probe: fails loudly before the per-monitor loop.
        arrived_at = file_last_modified(
            conn_type=self._conn_type, config=self._config, path=table, secret=self._secret
        )
        # One-slot memo of the READ ATTEMPT — not of the frame. Memoizing only
        # successes leaves a failure unmemoised, so each later monitor retries the
        # whole download: five monitors against a failing 2 GB object = five full
        # downloads, and a transient failure yields inconsistent outcomes within one
        # run (monitor 1 errored, monitor 3 fine, same file, same instant). A
        # DataFrame is not None-comparable, hence a list rather than a sentinel.
        attempt: list[Any] = []
        counted: list[Any] = []

        def _memoized(memo: list[Any], read: Any) -> Any:
            if not memo:
                try:
                    memo.append(read())
                except Exception as exc:
                    # Classified, never echoed: this message is persisted to
                    # `results` and rendered in the UI/alerts/MCP, and object-store
                    # auth errors have carried credentials in their text (#828).
                    log.warning(
                        "flatfile_monitor_read_failed",
                        connection_type=self._conn_type,
                        error_type=type(exc).__name__,
                    )
                    memo.append(FlatFileReadError(f"could not read {table!r} from the store"))
            if isinstance(memo[0], FlatFileReadError):
                raise memo[0]
            return memo[0]

        def dataframe() -> Any:
            return _memoized(
                attempt,
                lambda: read_dataframe(
                    conn_type=self._conn_type,
                    config=self._config,
                    path=table,
                    secret=self._secret,
                ),
            )

        def _wants_frame(spec: MonitorSpec) -> bool:
            if spec.kind != FRESHNESS:
                return False
            try:
                return freshness_column(spec.config) is not None
            except MonitorConfigError:
                # A malformed column config is THIS monitor's error, raised inside
                # `run_monitor_specs`' per-monitor guard so its siblings still run.
                # Letting it escape here — outside that guard — would fail the whole
                # run and persist nothing, which is the isolation contract
                # `run_monitor_specs` exists to keep.
                return False

        # Decided up front, not per monitor: if anything in this batch will pull
        # the frame anyway, volume should read it off that frame rather than issue
        # a second, independent read of the same object.
        frame_is_needed = any(_wants_frame(spec) for spec in monitors)

        def rows() -> int:
            if frame_is_needed:
                return len(dataframe())
            # Memoized for the same reason the frame is: several volume monitors
            # (different thresholds on one target) must not each re-scan a CSV,
            # and a failing read must not be retried once per monitor.
            return int(
                _memoized(
                    counted,
                    lambda: row_count(
                        conn_type=self._conn_type,
                        config=self._config,
                        path=table,
                        secret=self._secret,
                    ),
                )
            )

        def scalar_for(spec: MonitorSpec) -> Any:
            if spec.kind == VOLUME:
                return rows()
            column = freshness_column(spec.config)
            if column is None:
                return arrived_at
            df = dataframe()
            if column not in df.columns:
                raise MonitorConfigError(f"freshness column {column!r} is not in {table!r}")
            # None (an all-null column) routes through the shared "can't be
            # assessed" error rather than being read as age zero.
            return max_timestamp(df[column], column=column)

        return run_monitor_specs(scalar_for, monitors=monitors, now=datetime.now(UTC))

    def run_checks(
        self,
        *,
        table: str,
        schema: str | None,
        checks: list[CheckSpec],
        index_columns: list[str] | None = None,
    ) -> SuiteOutcome:
        df = read_dataframe(
            conn_type=self._conn_type, config=self._config, path=table, secret=self._secret
        )
        context = gx.get_context(mode="ephemeral")
        asset = context.data_sources.add_pandas(name="flatfile").add_dataframe_asset(name="file")
        # The pandas asset takes its batch at run time via batch_parameters; the
        # ephemeral context makes the fixed suite/vd names safe across runs.
        batch_definition = asset.add_batch_definition_whole_dataframe(name="whole_dataframe")
        return run_expectations(
            context,
            batch_definition=batch_definition,
            checks=checks,
            name="suite-flatfile",
            batch_parameters={"dataframe": df},
            index_columns=index_columns,
        )


def build_flatfile_runner(
    *, conn_type: str, config: dict[str, Any], secret_ref: str | None, secret_store: SecretStore
) -> FlatFileCheckRunner:
    """Build a runner from a flat-file `Connection`'s primitives.

    Mirrors `build_snowflake_runner`: resolves the secret eagerly and takes the
    raw config (not the ORM model) to keep the adapter decoupled from `db/`.
    """
    if conn_type not in _FILE_TYPES:
        raise ValueError(f"{conn_type!r} is not a flat-file datasource")
    if not secret_ref:
        raise ValueError("flat-file connection requires secret_ref for the credential")
    secret = secret_store.get(secret_ref)
    return FlatFileCheckRunner(conn_type=conn_type, config=config, secret=secret)


# ───────────────────────── batch resolution ────────────────────────
#
# Flat files usually arrive in batches — `orders_2026-06-01.csv`,
# `orders_2026-06-02.csv`, … — and a check targets *one* batch. The batch
# pattern is a regex whose **first capture group is the batch key**; `latest`
# selects the greatest key, `specific` selects a named key. Resolution (filter +
# select) is pure and fully tested; only the object listing is a live seam.


class BatchNotFoundError(ValueError):
    """No file matched the batch pattern (or the requested specific batch)."""


@dataclass(frozen=True)
class FileRef:
    """A listed object: its full key/blob path and last-modified time (if any)."""

    path: str
    last_modified: datetime | None = None


class BatchListingTooLargeError(ValueError):
    """The batch prefix lists more objects than resolution will scan (#943).

    Deliberately **not** a `BatchNotFoundError`: that means "the data hasn't
    landed" and skips the run (#122). This means "we refuse to answer", which is
    a failure — reporting a batch chosen from a partial scan would be a
    confidently wrong answer, since both stores list ascending and the newest key
    is at the end we did not reach.
    """


def _most_recent(files: list[FileRef]) -> str:
    """Path of the most recently modified file (ties broken by path; `files` non-empty)."""
    return max(files, key=lambda f: (f.last_modified or _MIN_DT, f.path)).path


def _rank(file: FileRef) -> tuple[datetime, str]:
    """Recency ordering key — the streaming equivalent of `_most_recent`'s."""
    return (file.last_modified or _MIN_DT, file.path)


def resolve_batch(
    files: Iterable[FileRef], *, pattern: str, strategy: str = "latest", batch: str | None = None
) -> str:
    """Pick one file's path from `files` per the batch `pattern` + `strategy`.

    `pattern` is a regex `re.search`-ed against each path; its first capture group
    (if any) is the batch key. `strategy`:

    * ``latest`` — the greatest batch key (lexicographic — ISO dates sort right),
      or, when the pattern has no capture group, the most recently modified file.
    * ``specific`` — the file whose batch key equals `batch` (required).

    Raises `BatchNotFoundError` (nothing matched / no such batch) or `ValueError`
    (bad strategy, or `specific` without `batch`).

    Takes an **iterable** and consumes it in a single pass, holding only the
    running best rather than a list of every matching object (#943): this runs on
    every scheduled run of every batch-targeted suite, and a prefix with a long
    history of dated files grew the retained list without bound. The three
    running bests below reproduce the previous `max()` calls exactly, including
    their first-wins tie-breaking (a strict ``>`` keeps the first maximal element,
    which is what `max` returns).
    """
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"invalid batch pattern {pattern!r}: {exc}") from exc

    saw_match = False
    best_keyed: tuple[str, str] | None = None  # (batch key, path)
    best_recent: tuple[datetime, str] | None = None  # over ALL matches
    best_of_batch: tuple[datetime, str] | None = None  # over `specific` hits

    for file in files:
        match = compiled.search(file.path)
        if match is None:
            continue
        saw_match = True
        # A batch key is the first capture group when it *participated* in the
        # match; an optional group that didn't (`None`) has no key, so it falls
        # through to the modified-time ordering rather than crashing a comparison
        # on None vs str.
        key = match.group(1) if match.groups() else None
        rank = _rank(file)
        if key is not None and (best_keyed is None or key > best_keyed[0]):
            best_keyed = (key, file.path)
        if best_recent is None or rank > best_recent:
            best_recent = rank
        if key is not None and key == batch and (best_of_batch is None or rank > best_of_batch):
            best_of_batch = rank

    # Checks stay in their original order: "nothing matched" is reported before an
    # invalid strategy, exactly as the list-building version did.
    if not saw_match:
        raise BatchNotFoundError(f"no files matched batch pattern {pattern!r}")

    if strategy == "specific":
        if batch is None:
            raise ValueError("strategy 'specific' requires a batch key")
        if best_of_batch is None:
            raise BatchNotFoundError(f"no file for batch {batch!r} under pattern {pattern!r}")
        return best_of_batch[1]

    if strategy != "latest":
        raise ValueError(f"unknown batch strategy {strategy!r}")

    if best_keyed is not None:
        return best_keyed[1]
    # `saw_match` guarantees at least one match, so this is never None here.
    return best_recent[1] if best_recent else ""


def iter_files(
    *, conn_type: str, config: dict[str, Any], prefix: str, secret: str
) -> Generator[FileRef]:
    """Stream objects/blobs under `prefix` on a flat-file datasource (live seam).

    A generator, so a caller that only needs a running maximum never holds the
    whole listing (#943). The ADLS client is released in a ``finally``, which runs
    on exhaustion **and** on ``close()`` — so a caller that abandons the iterator
    early must close it (`resolve_batch_file` does, via `contextlib.closing`)
    rather than leave the connection pool to the garbage collector.
    """
    if conn_type == "s3":
        cfg = S3Config.model_validate(config)
        paginator = _s3_client(cfg, secret).get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=cfg.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                yield FileRef(path=obj["Key"], last_modified=obj.get("LastModified"))
        return

    acfg = AdlsConfig.model_validate(config)
    client_az = _blob_service(acfg, secret)
    try:
        container = client_az.get_container_client(acfg.container)
        for blob in container.list_blobs(name_starts_with=prefix):
            yield FileRef(path=blob.name, last_modified=getattr(blob, "last_modified", None))
    finally:
        client_az.close()


def list_files(
    *, conn_type: str, config: dict[str, Any], prefix: str, secret: str
) -> list[FileRef]:
    """The whole listing under `prefix` as a list (live seam).

    Kept for callers that genuinely want every entry; batch resolution does not,
    and uses `iter_files` instead (#943).
    """
    return list(iter_files(conn_type=conn_type, config=config, prefix=prefix, secret=secret))


#: Object counts at which a batch prefix stops being reasonable. The soft limit
#: only makes the cost VISIBLE — a listing that has quietly grown to six figures
#: on a per-run path is worth knowing about before it becomes an incident (#839:
#: a silent cost reads as no cost). The hard limit refuses: past it, resolution
#: would spend minutes listing on every scheduled run, and answering from a
#: partial scan is not an option because both stores list ascending, so the
#: newest key — the one `latest` wants — is precisely what a truncated scan misses.
_BATCH_LISTING_WARN_AT = 50_000
_BATCH_LISTING_MAX = 500_000


def _counted(files: Iterable[FileRef], *, prefix: str, limit: int) -> Iterator[FileRef]:
    """Pass `files` through, refusing past `limit` objects."""
    for index, file in enumerate(files, start=1):
        if index > limit:
            log.error("flatfile_batch_listing_too_large", scanned=index - 1, limit=limit)
            raise BatchListingTooLargeError(
                f"batch prefix {prefix!r} lists more than {limit} objects; narrow the "
                "prefix (e.g. add a year/month segment) so resolution stays bounded"
            )
        yield file


def resolve_batch_file(
    *,
    conn_type: str,
    config: dict[str, Any],
    secret: str,
    prefix: str,
    pattern: str,
    strategy: str = "latest",
    batch: str | None = None,
) -> str:
    """Stream the listing under `prefix` and resolve the batch file path.

    The listing is consumed lazily (#943) — `resolve_batch` keeps only its running
    bests, so memory no longer grows with the prefix's history. `closing` releases
    the ADLS client even when resolution raises part-way through.
    """
    scanned = 0

    def _tally(files: Iterable[FileRef]) -> Iterator[FileRef]:
        nonlocal scanned
        for file in files:
            scanned += 1
            yield file

    stream = iter_files(conn_type=conn_type, config=config, prefix=prefix, secret=secret)
    try:
        with closing(stream):
            return resolve_batch(
                _tally(_counted(stream, prefix=prefix, limit=_BATCH_LISTING_MAX)),
                pattern=pattern,
                strategy=strategy,
                batch=batch,
            )
    finally:
        # Only when the listing was large but still answered: a refused listing has
        # already logged its own error, and two lines about one event reads as two.
        if _BATCH_LISTING_WARN_AT <= scanned < _BATCH_LISTING_MAX:
            log.warning("flatfile_batch_listing_large", scanned=scanned, conn_type=conn_type)
