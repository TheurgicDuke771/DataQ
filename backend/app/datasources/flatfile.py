"""Flat-file (ADLS Gen2 / S3) IO + GX `CheckRunner`."""

from __future__ import annotations

import csv
import io
import re
import time
from collections.abc import Callable, Generator, Iterable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar

import great_expectations as gx

from backend.app.core.config import get_settings
from backend.app.core.errors import SafeMonitorError
from backend.app.core.logging import get_logger
from backend.app.core.s3_endpoint import addressing_config_kwargs
from backend.app.core.secrets import SecretStore
from backend.app.datasources.adls import AdlsConfig
from backend.app.datasources.base import (
    SAMPLE_HEAD,
    CheckOutcome,
    CheckSpec,
    MonitorSpec,
    SampleSpec,
    SuiteOutcome,
)
from backend.app.datasources.gx_runner import run_expectations
from backend.app.datasources.monitors import (
    FRESHNESS,
    VOLUME,
    MonitorConfigError,
    freshness_column,
    run_monitor_specs,
)
from backend.app.datasources.s3 import S3Config
from backend.app.datasources.sampling import (
    SamplingDrawError,
    batches_to_frame,
    enforce_byte_cap,
    enforce_sample_cap,
    merge_by_position,
    reservoir_sample,
    sample_row_indices,
    sampling_record,
    split_row_count_checks,
    stamp_sampling,
    take_head,
    take_indices,
)

# Connector timeouts (s). _READ_TIMEOUT deliberately exceeds the SQL profiler's
# 30s — it covers a full-object download, not one query; not drift (#147).
_CONNECT_TIMEOUT = 10
_READ_TIMEOUT = 60

log = get_logger(__name__)

_FILE_TYPES = {"adls_gen2", "s3"}

# Sort floor for files the store reports without a modified time.
_MIN_DT = datetime.min.replace(tzinfo=UTC)

# Sniffer allowlist: an unconstrained csv.Sniffer nominates letters/spaces as
# delimiters. Fallback comma = pre-#476 behaviour, so a failed sniff never regresses.
_CSV_DELIMITERS = ",;\t|"
_DEFAULT_DELIMITER = ","

# Bytes handed to the sniffer — bounds the decode on a large file.
_SNIFF_BYTES = 64 * 1024

#: Window size for sequential walks (CSV count, Parquet profiler sample #1001).
#: Public: `profile_service` reuses it for the same reason `csv_row_count` does.
STREAM_CHUNK = 8 * 1024 * 1024

#: Head bytes to type/name a CSV's columns — keeps schema introspection off the
#: full-object path (#882).
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
    """Guess a CSV's delimiter from its leading bytes, falling back to a comma."""
    text = sample.decode("utf-8", errors="replace")
    # Sniff whole lines only: a mid-row fragment skews Sniffer's field counts.
    head, newline, _ = text.rpartition("\n")
    if newline:
        text = head
    if not text.strip():
        return _DEFAULT_DELIMITER
    try:
        return csv.Sniffer().sniff(text, delimiters=_CSV_DELIMITERS).delimiter
    except csv.Error:
        return _DEFAULT_DELIMITER


def sniff_from_reader(reader: RangeReader) -> str:
    """Sniff a CSV's delimiter off ``reader``'s first chunk and rewind it (#1329).

    The stream's own first window already covers these bytes, so reading them
    here costs no request where a separate `read_range` cost one per read.
    """
    head = reader.read(_SNIFF_BYTES)
    reader.seek(0)
    return sniff_delimiter(head)


def open_csv_stream(reader: RangeReader) -> Any:
    """The ONE Arrow CSV stream configuration over a `RangeReader` (#1330).

    Counting a CSV and taking rows from it walk separate streams; if their parse
    options ever differed, the positions drawn against one numbering would be read
    out of another — an off-by-N sample with no symptom.
    """
    import pyarrow.csv as pv

    return pv.open_csv(reader, parse_options=pv.ParseOptions(delimiter=sniff_from_reader(reader)))


def trim_to_row_boundary(raw: bytes) -> bytes:
    """Cut ``raw`` at the last quote-safe newline (#595 C4)."""
    end = len(raw)
    while True:
        cut = raw.rfind(b"\n", 0, end)
        if cut == -1:
            return raw
        if raw.count(b'"', 0, cut) % 2 == 0:
            return raw[:cut]
        end = cut


def read_csv_bytes(raw: io.BytesIO, **kwargs: Any) -> Any:
    """`pd.read_csv` over `raw` with the delimiter sniffed from its header (#476)."""
    import pandas as pd

    # `read`, not `getvalue()[:n]` — getvalue copies the ENTIRE buffer first,
    # transiently doubling peak RSS on a large CSV.
    raw.seek(0)
    sep = sniff_delimiter(raw.read(_SNIFF_BYTES))
    raw.seek(0)
    return pd.read_csv(raw, sep=sep, **kwargs)


def _s3_client(cfg: S3Config, secret: str) -> Any:
    """The one boto3 S3 client every S3 read path uses (S3-compatible endpoint #1063)."""
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


class StoreSession:
    """One store client, reused across the reads of a single logical operation (#1329).

    A range-read walk issues many requests where the pre-#882 code issued one
    download, and a client per request is pure overhead on that pattern. The
    session is request-scoped and NOT thread-safe: it is created inside the read
    that uses it and closed with it, so no client is ever module state a Celery
    prefork child could inherit.
    """

    def __init__(self, *, conn_type: str, config: dict[str, Any], secret: str) -> None:
        self.conn_type = conn_type
        self._config = config
        self._secret = secret
        self._client: Any = None
        self._s3_config: S3Config | None = None
        self._adls_config: AdlsConfig | None = None
        #: Clients constructed by this session — asserted by the seam tests.
        self.clients_created = 0
        #: Byte sizes already known per path, for THIS session's lifetime only —
        #: it dedups the count→take pair of one sampled read (#1329), never
        #: seeded from a caller's own, possibly stale, stat (#2004).
        self.sizes: dict[str, int] = {}

    @property
    def s3_config(self) -> S3Config:
        if self._s3_config is None:
            self._s3_config = S3Config.model_validate(self._config)
        return self._s3_config

    @property
    def adls_config(self) -> AdlsConfig:
        if self._adls_config is None:
            self._adls_config = AdlsConfig.model_validate(self._config)
        return self._adls_config

    @property
    def s3(self) -> Any:
        if self._client is None:
            self._client = _s3_client(self.s3_config, self._secret)
            self.clients_created += 1
        return self._client

    @property
    def blob_service(self) -> Any:
        if self._client is None:
            self._client = _blob_service(self.adls_config, self._secret)
            self.clients_created += 1
        return self._client

    def blob(self, path: str) -> Any:
        """A blob client for ``path`` on this session's service client."""
        return self.blob_service.get_blob_client(container=self.adls_config.container, blob=path)

    def close(self) -> None:
        client, self._client = self._client, None
        closer = getattr(client, "close", None)
        if closer is not None:
            closer()

    def __enter__(self) -> StoreSession:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


@contextmanager
def _session(
    session: StoreSession | None, *, conn_type: str, config: dict[str, Any], secret: str
) -> Iterator[StoreSession]:
    """Yield ``session``, or a throwaway one closed on the way out."""
    if session is not None:
        yield session
        return
    with StoreSession(conn_type=conn_type, config=config, secret=secret) as own:
        yield own


def download_bytes(
    *,
    conn_type: str,
    config: dict[str, Any],
    path: str,
    secret: str,
    session: StoreSession | None = None,
) -> bytes:
    """Fetch the object/blob bytes from S3 or ADLS Gen2 (live seam)."""
    with _session(session, conn_type=conn_type, config=config, secret=secret) as ses:
        if conn_type == "s3":
            body: bytes = ses.s3.get_object(Bucket=ses.s3_config.bucket, Key=path)["Body"].read()
            return body
        downloaded: bytes = ses.blob(path).download_blob().readall()
        return downloaded


def object_size(
    *,
    conn_type: str,
    config: dict[str, Any],
    path: str,
    secret: str,
    session: StoreSession | None = None,
) -> int:
    """Byte length of exactly ``path`` — one metadata call (live seam, #882).

    Answered from the session's size memo when it already holds ``path``.
    """
    with _session(session, conn_type=conn_type, config=config, secret=secret) as ses:
        known = ses.sizes.get(path)
        if known is not None:
            return known
        size = _head_stat(ses, path).size
        if size is None:
            raise FlatFileReadError(f"the store reported no byte length for {path!r}")
        ses.sizes[path] = size
        return size


def read_range(
    *,
    conn_type: str,
    config: dict[str, Any],
    path: str,
    secret: str,
    start: int,
    length: int,
    session: StoreSession | None = None,
) -> bytes:
    """``length`` bytes of ``path`` from offset ``start`` (live seam, #882)."""
    if length <= 0:
        return b""
    with _session(session, conn_type=conn_type, config=config, secret=secret) as ses:
        if conn_type == "s3":
            # Inclusive end, per RFC 7233 — `bytes=0-1023` is the first 1024 bytes.
            body: bytes = ses.s3.get_object(
                Bucket=ses.s3_config.bucket,
                Key=path,
                Range=f"bytes={start}-{start + length - 1}",
            )["Body"].read()
            return body
        downloaded: bytes = ses.blob(path).download_blob(offset=start, length=length).readall()
        return downloaded


class RangeReader(io.RawIOBase):
    """A seekable file over an object store, backed by range GETs (#882/#942)."""

    #: Default bytes per request, sized for SEEKING access (Parquet footer).
    _CHUNK = 256 * 1024

    def __init__(
        self,
        *,
        conn_type: str,
        config: dict[str, Any],
        path: str,
        secret: str,
        chunk: int | None = None,
        session: StoreSession | None = None,
    ) -> None:
        self._conn_type = conn_type
        self._config = config
        self._path = path
        self._secret = secret
        self._chunk = chunk or self._CHUNK
        self._owns_session = session is None
        self._session = session or StoreSession(conn_type=conn_type, config=config, secret=secret)
        self._size = object_size(
            conn_type=conn_type, config=config, path=path, secret=secret, session=self._session
        )
        self._pos = 0
        self._window = b""
        self._window_start = 0
        #: Range requests issued — asserted by tests to verify the cheap path.
        self.requests = 0

    # ── io.RawIOBase surface pyarrow/pandas need ──────────────────────────
    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def close(self) -> None:
        try:
            if self._owns_session:
                self._session.close()
        finally:
            super().close()

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
        # Clamp so a read near the end never asks past EOF.
        span = min(span, self._size - start)
        self._window = read_range(
            conn_type=self._conn_type,
            config=self._config,
            path=self._path,
            secret=self._secret,
            start=start,
            length=span,
            session=self._session,
        )
        self._window_start = start
        self.requests += 1
        if len(self._window) < span:
            self._size = start + len(self._window)


def parquet_row_count(
    *,
    conn_type: str,
    config: dict[str, Any],
    path: str,
    secret: str,
    session: StoreSession | None = None,
) -> int:
    """Row count from the Parquet footer — a couple of range GETs, no data read (#942)."""
    import pyarrow.parquet as pq

    reader = RangeReader(
        conn_type=conn_type, config=config, path=path, secret=secret, session=session
    )
    with closing(reader):
        rows: int = pq.ParquetFile(reader).metadata.num_rows
    return rows


def csv_row_count(
    *,
    conn_type: str,
    config: dict[str, Any],
    path: str,
    secret: str,
    session: StoreSession | None = None,
) -> int:
    """Row count of a CSV, streamed in batches — never a full DataFrame (#942)."""
    # Big window: this walks end to end; the seeking default would mean
    # thousands of range requests.
    reader = RangeReader(
        conn_type=conn_type,
        config=config,
        path=path,
        secret=secret,
        chunk=STREAM_CHUNK,
        session=session,
    )
    with closing(reader):
        with open_csv_stream(reader) as batches:
            # The header is consumed by the reader, so batch rows are data rows.
            return sum(batch.num_rows for batch in batches)


def _extend_window(reader_args: dict[str, Any], buffered: bytearray, *, span: int) -> bool:
    """Grow ``buffered`` to ``span`` bytes, fetching only the delta (#1329), and
    report whether the store returned less than asked — i.e. EOF.
    """
    if span <= len(buffered):
        return False
    asked = span - len(buffered)
    got = read_range(**reader_args, start=len(buffered), length=asked)
    buffered += got
    return len(got) < asked


def _window_frame(
    buffered: bytearray, *, limit: int, reached_eof: bool, usecols: CsvUseCols = None
) -> Any:
    """Parse at most ``limit`` rows out of a head window, dropping the trailing
    partial row unless the window reached EOF (#595 C4). ``usecols`` — anything
    `pandas.read_csv` accepts, e.g. a membership callable — projects at parse
    time (#2000), same as the profiler's old full-download read did.
    """
    raw = bytes(buffered)
    if not reached_eof:
        raw = trim_to_row_boundary(raw)
    kwargs: dict[str, Any] = {"nrows": limit}
    if usecols is not None:
        kwargs["usecols"] = usecols
    return read_csv_bytes(io.BytesIO(raw), **kwargs)


#: What `pandas.read_csv`'s own ``usecols`` accepts — a membership test or an
#: explicit name list, never an arbitrary value the type checker can't narrow.
CsvUseCols = Callable[[str], bool] | Iterable[str] | None


def _reader_args(
    *,
    conn_type: str,
    config: dict[str, Any],
    path: str,
    secret: str,
    session: StoreSession | None = None,
) -> dict[str, Any]:
    """The kwargs every store-read helper (`object_size`/`read_range`/`RangeReader`)
    takes, built once so the shape can't drift between call sites (#2000 review).
    """
    return {
        "conn_type": conn_type,
        "config": config,
        "path": path,
        "secret": secret,
        "session": session,
    }


def read_csv_head(
    *,
    conn_type: str,
    config: dict[str, Any],
    path: str,
    secret: str,
    rows: int,
    session: StoreSession | None = None,
) -> Any:
    """Parse the first ``rows`` data rows of a CSV from a bounded head read (#882).

    One fixed window of `_csv_head_frame`'s growing walk (#1330): same delta fetch,
    same short-read-is-EOF rule, same quote-aware trim — they were two copies of
    one read and #1325 had to fix the same defect in both.
    """
    reader_args = _reader_args(
        conn_type=conn_type, config=config, path=path, secret=secret, session=session
    )
    buffered = bytearray()
    # One byte MORE than the window, so a short read unambiguously means EOF —
    # a file of exactly window size must not be mistaken for a cut one.
    reached_eof = _extend_window(reader_args, buffered, span=_CSV_HEAD_BYTES + 1)
    return _window_frame(buffered, limit=rows, reached_eof=reached_eof)


def row_count(
    *,
    conn_type: str,
    config: dict[str, Any],
    path: str,
    secret: str,
    session: StoreSession | None = None,
) -> int:
    """Rows in a flat file by the cheapest route: Parquet footer or CSV stream (#942)."""
    fmt = format_from_path(path)
    if fmt is None:
        raise ValueError(f"unsupported flat-file format for path {path!r}")
    counter = csv_row_count if fmt == "csv" else parquet_row_count
    return counter(conn_type=conn_type, config=config, path=path, secret=secret, session=session)


def read_dataframe(*, conn_type: str, config: dict[str, Any], path: str, secret: str) -> Any:
    """Download and parse the whole file into pandas (live seam); `ValueError` on
    an unknown format. Checks need every row/column — counts must be exact.
    """
    import pandas as pd

    fmt = format_from_path(path)
    if fmt is None:
        raise ValueError(f"unsupported flat-file format for path {path!r}")
    raw = io.BytesIO(download_bytes(conn_type=conn_type, config=config, path=path, secret=secret))
    if fmt == "csv":
        return read_csv_bytes(raw)
    return pd.read_parquet(raw, dtype_backend="pyarrow")


#: Rows per Arrow batch while streaming a sampled read — keeps peak memory small.
_SAMPLE_BATCH_ROWS = 65_536


def _open_batch_stream(
    reader_args: dict[str, Any], fmt: str
) -> tuple[Any, Any, bool, Callable[[], None]]:
    """A forward stream of Arrow record batches over a flat file (live seam, #595)."""
    reader = RangeReader(**reader_args, chunk=STREAM_CHUNK)

    def _closer(inner: Callable[[], None]) -> Callable[[], None]:
        def close() -> None:
            try:
                inner()
            finally:
                reader.close()

        return close

    if fmt == "csv":
        stream = open_csv_stream(reader)
        return stream, stream.schema, False, _closer(stream.close)

    import pyarrow.parquet as pq

    parquet = pq.ParquetFile(reader)
    return (
        parquet.iter_batches(batch_size=_SAMPLE_BATCH_ROWS),
        parquet.schema_arrow,
        True,
        _closer(parquet.close),
    )


#: Sentinel distinguishing "use the configured scan-byte cap" (the default, every
#: caller today) from an explicit ``None`` opt-out for a caller that has already
#: bounded the read some other way.
_CONFIGURED_CAP = object()


def _csv_head_frame(
    reader_args: dict[str, Any],
    *,
    limit: int,
    usecols: CsvUseCols = None,
    max_window_bytes: int | None = _CONFIGURED_CAP,  # type: ignore[assignment]
) -> Any:
    """The first ``limit`` rows of a CSV via a doubling byte range (#595).

    Each growth fetches only the bytes past what is already buffered (#1329):
    re-reading the prefix made reaching 4 MB cost 1 + 2 + 4 = 7 MB. A frame
    SHORTER than ``limit`` therefore means the walk reached EOF — the only
    other way out of the loop is ``max_window_bytes`` (#2000): the window is
    NOT allowed to grow past it while there is still more file to read and the
    target row count isn't met yet, whether that is because a single row is
    malformed/unterminated or simply because the rows are legitimately wide —
    either way, silently reading further would be the unbounded whole-object
    walk this function exists to avoid, so it raises a classified
    `FlatFileReadError` instead of a) growing without limit or b) returning a
    partial frame that a caller (e.g. the suite-run sample's ``truncated``
    flag) would then have no way to distinguish from a legitimate EOF.

    ``max_window_bytes <= 0`` (default `RUN_MAX_SCAN_BYTES`, so 0 only via an
    explicit override) disables the check entirely — the walk is then bounded
    only by the object's own size, matching this function's pre-#2000
    behaviour.

    Defaults to ``RUN_MAX_SCAN_BYTES`` (every caller today: the suite-run
    sampled path in `_sampled_frame` and the profiler's `read_csv_projected_sample`
    both need the same guard, not just the one that asked first) — pass
    ``max_window_bytes=None`` explicitly to opt out for a caller that has
    already bounded the read some other way. This reuses the existing
    whole-object scan cap rather than adding a second setting; it now also
    means "the head-window ceiling before a bounded CSV read gives up", which
    is documented here and in the env var reference rather than left implicit.
    """
    if max_window_bytes is _CONFIGURED_CAP:
        max_window_bytes = get_settings().run_max_scan_bytes
    size = object_size(**reader_args)
    buffered = bytearray()
    window = _CSV_HEAD_BYTES
    while True:
        span = min(window, size)
        reached_eof = _extend_window(reader_args, buffered, span=span) or span >= size
        frame = _window_frame(buffered, limit=limit, reached_eof=reached_eof, usecols=usecols)
        if len(frame) >= limit or reached_eof:
            return frame
        if max_window_bytes is not None and max_window_bytes > 0 and span >= max_window_bytes:
            raise FlatFileReadError(
                f"could not reach the requested sample within the {max_window_bytes:,}-byte "
                "scan cap — the file may have a malformed/unterminated row, or its rows are "
                "wide enough that RUN_MAX_SCAN_BYTES needs raising for this target row count"
            )
        window *= 2


def read_csv_projected_sample(
    *,
    conn_type: str,
    config: dict[str, Any],
    path: str,
    secret: str,
    rows: int,
    usecols: CsvUseCols = None,
) -> Any:
    """The column profiler's bounded CSV read (#2000): the first ``rows`` data
    rows, projected to ``usecols`` at parse time, via the same doubling head
    window `read_sampled_dataframe` uses for a suite run — never the whole
    object, which the profiler used to download just to keep a handful of
    columns from its first 100k rows.

    ``RUN_MAX_SCAN_BYTES`` caps the window growth: a target that can't be
    reached within that budget raises a classified `FlatFileReadError`
    (surfaced as `ProfileFailedError` by `profile_file`) instead of silently
    walking the window out to the object's own size — a failure mode the
    profiler could not previously produce for a CSV, since the old
    whole-object read either succeeded or failed on the download itself.
    """
    with StoreSession(conn_type=conn_type, config=config, secret=secret) as session:
        reader_args = _reader_args(
            conn_type=conn_type, config=config, path=path, secret=secret, session=session
        )
        return _csv_head_frame(reader_args, limit=rows, usecols=usecols)


def read_sampled_dataframe(
    *,
    conn_type: str,
    config: dict[str, Any],
    path: str,
    secret: str,
    sample: SampleSpec,
) -> tuple[Any, dict[str, Any]]:
    """A bounded sample of a flat file, plus the record of what was sampled (#595).

    Always probes the object's OWN metadata fresh rather than accepting a
    caller's already-fetched stat — a runner's stat memo can predate this read
    by a whole checks phase, and seeding from it silently bounded a sample (and
    its `total_rows`) to a size the object may have long since grown past
    (#2004). Uniform with `_counted_rows`: the read's whole job is to describe
    the object as it stands now.
    """
    fmt = format_from_path(path)
    if fmt is None:
        raise ValueError(f"unsupported flat-file format for path {path!r}")
    with StoreSession(conn_type=conn_type, config=config, secret=secret) as session:
        return _sampled_frame(
            _reader_args(
                conn_type=conn_type, config=config, path=path, secret=secret, session=session
            ),
            fmt=fmt,
            path=path,
            sample=sample,
        )


def _sampled_frame(
    reader_args: dict[str, Any], *, fmt: str, path: str, sample: SampleSpec
) -> tuple[Any, dict[str, Any]]:
    """`read_sampled_dataframe`'s body, over an already-open store session."""
    if sample.strategy == SAMPLE_HEAD and fmt == "csv":
        frame = _csv_head_frame(reader_args, limit=sample.rows + 1)
        truncated = len(frame) > sample.rows
        if truncated:
            frame = frame.head(sample.rows)
        # Not truncated implies the walk reached EOF, so the size is known free.
        csv_total = None if truncated else len(frame)
        return frame, sampling_record(
            sample, rows=len(frame), total_rows=csv_total, sampled=truncated
        )

    want_head = sample.strategy == SAMPLE_HEAD
    # A CSV has no cheap count, so counting it first meant walking the object
    # twice for one sample; the reservoir draws and counts in the same pass
    # (#1329). Parquet keeps the count — its footer read is not a pass.
    single_pass = not want_head and fmt == "csv"
    total: int | None = None
    indices: list[int] | None = None
    if not want_head and not single_pass:
        total = row_count(**reader_args)
        # `None` = sample covers the whole dataset — read straight through rather
        # than materialise an identity index list (~40 MB at 1.4M rows).
        indices = sample_row_indices(total=total, rows=sample.rows, seed=sample.seed)

    if indices is not None:
        reader_args["session"].sizes.pop(path, None)
    batches, schema, arrow_backed, close = _open_batch_stream(reader_args, fmt)
    try:
        if single_pass:
            taken, total = reservoir_sample(batches, rows=sample.rows, seed=sample.seed)
        elif indices is not None:
            taken = take_indices(batches, indices)
        else:
            # `rows + 1` for head (probe row); the counted total for an
            # everything-covering random sample.
            taken = take_head(batches, limit=sample.rows + 1 if want_head else (total or 0))
    finally:
        close()

    read_rows = sum(batch.num_rows for batch in taken)
    if single_pass:
        # `total` is the rows walked in this pass — the population as of the read
        # that produced the sample, not of an earlier counting pass.
        truncated = read_rows < (total or 0)
    elif indices is not None:
        # `total` is set on every path that produces indices.
        assert total is not None
        _require_complete_draw(read_rows, len(indices), path=path, total=total)
        truncated = sample.rows < (total or 0)
    elif want_head:
        truncated = read_rows > sample.rows
        if truncated:
            taken = take_head(taken, limit=sample.rows)
        else:
            # Stream ended inside the probe row: whole file fits, size known free.
            total = read_rows
    else:
        # A random sample that covered the whole dataset: complete, not a sample.
        truncated = False

    frame = batches_to_frame(taken, schema=schema, arrow_backed=arrow_backed)
    return frame, sampling_record(sample, rows=len(frame), total_rows=total, sampled=truncated)


def _require_complete_draw(taken: int, wanted: int, *, path: str, total: int) -> None:
    """Refuse a random sample whose object shrank between count and take (#595 J1)."""
    if taken < wanted:
        raise SamplingDrawError(
            f"{path!r} changed while it was being sampled — only {taken:,} of {wanted:,} "
            f"drawn rows were still present (it held {total:,} when counted). The sample "
            "would be short and skewed while the record still reported the old "
            "population, so DataQ refuses the run rather than report it as "
            "representative; re-run once the file has settled."
        )


class FlatFileReadError(SafeMonitorError, RuntimeError):
    """The object couldn't be downloaded/parsed — reason CLASSIFIED, never echoed."""


def _is_temporal(series: Any) -> bool:
    """Whether ``series`` holds date/time values, numpy- **or** Arrow-backed."""
    import pandas as pd

    if pd.api.types.is_datetime64_any_dtype(series):
        return True
    arrow_type = getattr(series.dtype, "pyarrow_dtype", None)
    if arrow_type is None:
        return False
    import pyarrow as pa

    return bool(pa.types.is_timestamp(arrow_type) or pa.types.is_date(arrow_type))


def max_timestamp(series: Any, *, column: str) -> Any:
    """The newest timestamp in ``series``, or ``None`` if it holds none (#520)."""
    import pandas as pd

    cleaned = series.dropna()
    if cleaned.empty:
        return None
    if _is_temporal(cleaned):  # numpy- or Arrow-backed; already an instant
        return cleaned.max()
    # Refuse on NUMERIC rather than accept-only-object — the inverted form
    # excluded Arrow-backed timestamps along with the numerics.
    if pd.api.types.is_numeric_dtype(cleaned) or pd.api.types.is_bool_dtype(cleaned):
        raise MonitorConfigError(
            f"freshness column {column!r} is {cleaned.dtype}, not a date/timestamp"
        )
    parsed = pd.to_datetime(cleaned, errors="coerce", utc=True).dropna()
    if len(parsed) * 2 < len(cleaned):
        raise MonitorConfigError(
            f"freshness column {column!r} is mostly not timestamps "
            f"({len(parsed)} of {len(cleaned)} values parsed) — check the column name"
        )
    return parsed.max()


@dataclass(frozen=True)
class FileStat:
    """One metadata call's answer: arrival time + size (both ``None`` if absent)."""

    last_modified: datetime | None = None
    size: int | None = None


def _head_stat(ses: StoreSession, path: str) -> FileStat:
    """The ONE metadata call behind both `file_stat` and `object_size` (#1330) —
    they asked the same store API for overlapping halves of one answer. A missing
    object raises here; `file_stat` is the caller that maps that to an empty stat.
    """
    if ses.conn_type == "s3":
        head = ses.s3.head_object(Bucket=ses.s3_config.bucket, Key=path)
        return FileStat(last_modified=head.get("LastModified"), size=head.get("ContentLength"))
    properties = ses.blob(path).get_blob_properties()
    return FileStat(last_modified=properties.last_modified, size=properties.size)


def file_stat(
    *,
    conn_type: str,
    config: dict[str, Any],
    path: str,
    secret: str,
    session: StoreSession | None = None,
) -> FileStat:
    """The store's metadata for exactly ``path`` (live seam, #520/#595)."""
    with _session(session, conn_type=conn_type, config=config, secret=secret) as ses:
        # `ses.conn_type`, not the parameter: `_session` yields a caller-supplied
        # session as-is, and the store API and its not-found mapping must be
        # chosen off the same source or a missing blob escapes the wrong `except`.
        if ses.conn_type == "s3":
            from botocore.exceptions import ClientError

            try:
                return _head_stat(ses, path)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                    return FileStat()
                raise

        from azure.core.exceptions import ResourceNotFoundError

        try:
            return _head_stat(ses, path)
        except ResourceNotFoundError:
            return FileStat()


class FlatFileCheckRunner:
    """`CheckRunner` for flat files — loads the file into pandas, runs GX on it."""

    supported_monitor_kinds: ClassVar[frozenset[str]] = frozenset({FRESHNESS, VOLUME})

    def __init__(
        self,
        *,
        conn_type: str,
        config: dict[str, Any],
        secret: str,
        sampling: SampleSpec | None = None,
    ) -> None:
        self._conn_type = conn_type
        self._config = config
        self._secret = secret
        self._sampling = sampling
        #: Per-path metadata memo (#595) — the guard can be reached twice per phase.
        self._stats: dict[str, FileStat] = {}

    def _begin_phase(self) -> None:
        """Drop the stat memo, so this phase probes the object as it stands now (#2007)."""
        self._stats.clear()

    def _stat(self, path: str) -> FileStat:
        """`file_stat` for ``path``, once per RUN PHASE — never across two (#2007).

        The rule: no decision about *now* — arrival-time freshness, a byte-cap
        verdict — may read a probe taken in an earlier phase, so every phase
        that can follow another opens with `_begin_phase`. Within one phase the
        establishment probe's answer IS shared, which is what keeps the scan
        guardrail free of a second call.

        `run_service` drives ONE runner through `run_checks` and then always
        `run_monitors`, so before #2007 a runner-lifetime memo answered the
        monitors phase with the object as it stood before the checks phase: an
        `arrived_at` from before the write that actually landed, and a byte-cap
        verdict on a size the object had already grown past. `run_checks` is
        first by construction (that order is fixed in `run_service`), so its own
        guard establishes the memo and has nothing stale to inherit.
        """
        stat = self._stats.get(path)
        if stat is None:
            stat = file_stat(
                conn_type=self._conn_type, config=self._config, path=path, secret=self._secret
            )
            self._stats[path] = stat
        return stat

    def _guard_object_size(self, path: str) -> None:
        """Refuse a full-object read exceeding ``RUN_MAX_SCAN_BYTES`` (#595).

        Re-probing within the phase is prevented by `_stat`'s memo alone (#1330)
        — a second don't-re-probe mechanism beside it was one too many. The memo
        never crosses a phase boundary, so the size weighed here is the object's
        as of this phase, not an earlier one's (#2007).
        """
        cap = get_settings().run_max_scan_bytes
        if cap <= 0:
            return
        size = self._stat(path).size
        if size is not None:
            enforce_byte_cap(size, cap=cap, target=f"file {path!r}")

    def _counted_rows(self, path: str) -> int:
        """`row_count` over one store session (#1329) — the CSV walk behind a volume
        monitor is many range reads, not one download. Deliberately NOT seeded with
        the runner's stat: the count is the one number whose job is to be current.
        """
        with StoreSession(
            conn_type=self._conn_type, config=self._config, secret=self._secret
        ) as session:
            return row_count(
                conn_type=self._conn_type,
                config=self._config,
                path=path,
                secret=self._secret,
                session=session,
            )

    def _load_frame(self, path: str) -> tuple[Any, dict[str, Any] | None]:
        """The frame the checks run against, plus its sampling record (or ``None``)."""
        if self._sampling is not None:
            enforce_sample_cap(self._sampling, cap=get_settings().run_max_scan_rows)
            return read_sampled_dataframe(
                conn_type=self._conn_type,
                config=self._config,
                path=path,
                secret=self._secret,
                sample=self._sampling,
            )
        self._guard_object_size(path)
        frame = read_dataframe(
            conn_type=self._conn_type, config=self._config, path=path, secret=self._secret
        )
        return frame, None

    def run_monitors(
        self, *, table: str, schema: str | None, monitors: list[MonitorSpec]
    ) -> list[CheckOutcome]:
        """Evaluate freshness/volume monitors on a flat file — no SQL (#520)."""
        # Establishment probe — fails loudly before the per-monitor loop; also
        # carries the size, so the scan guardrail costs no second call (#595).
        # Fresh for this phase (#2007): both decisions it feeds are about now.
        self._begin_phase()
        stat = self._stat(table)
        arrived_at = stat.last_modified
        # One-slot memo of the READ ATTEMPT, failures included — otherwise each monitor retries the
        # download and outcomes diverge within one run.
        attempt: list[Any] = []
        counted: list[Any] = []

        def _memoized(memo: list[Any], read: Any) -> Any:
            if not memo:
                try:
                    memo.append(read())
                except Exception as exc:
                    # Classified, never echoed — the message persists to results/
                    # UI/alerts/MCP and SDK errors have carried credentials (#828).
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
            # Guardrail raised OUTSIDE `_memoized` — its except would fold the actionable over-cap
            # message into the vague FlatFileReadError.
            self._guard_object_size(table)
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
                # A malformed column config must error inside run_monitor_specs'
                # per-monitor guard — escaping here would fail the whole run.
                return False

        # Decided up front: if anything will pull the frame anyway, volume reads
        # off it instead of issuing a second read of the same object.
        frame_is_needed = any(_wants_frame(spec) for spec in monitors)

        def rows() -> int:
            if frame_is_needed:
                return len(dataframe())
            # Memoized like the frame: no per-monitor re-scans or read retries.
            return int(_memoized(counted, lambda: self._counted_rows(table)))

        def scalar_for(spec: MonitorSpec) -> Any:
            if spec.kind == VOLUME:
                return rows()
            column = freshness_column(spec.config)
            if column is None:
                return arrived_at
            df = dataframe()
            if column not in df.columns:
                raise MonitorConfigError(f"freshness column {column!r} is not in {table!r}")
            # None (all-null column) routes to "can't be assessed", not age zero.
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
        # Row-count expectations against a sampled frame measure the SAMPLE
        # (#595 C6) — refused per check, only when sampling is on.
        refused: dict[int, CheckOutcome] = {}
        runnable = list(range(len(checks)))
        if self._sampling is not None:
            runnable, refused = split_row_count_checks(checks)

        df, sampling = self._load_frame(table)
        context = gx.get_context(mode="ephemeral")
        asset = context.data_sources.add_pandas(name="flatfile").add_dataframe_asset(name="file")
        # Batch arrives via batch_parameters; ephemeral context makes fixed names safe.
        batch_definition = asset.add_batch_definition_whole_dataframe(name="whole_dataframe")
        outcome = run_expectations(
            context,
            batch_definition=batch_definition,
            checks=[checks[i] for i in runnable],
            name="suite-flatfile",
            batch_parameters={"dataframe": df},
            index_columns=index_columns,
        )
        # Stamped on every outcome (#595). REFUSALS deliberately unstamped — the
        # record describes a read and a refused check performed none.
        stamped = stamp_sampling(outcome, sampling)
        if not refused:
            return stamped
        merged = merge_by_position(
            len(checks), (runnable, stamped.checks), (list(refused), list(refused.values()))
        )
        return SuiteOutcome(success=False, checks=merged)


def build_flatfile_runner(
    *,
    conn_type: str,
    config: dict[str, Any],
    secret_ref: str | None,
    secret_store: SecretStore,
    sampling: SampleSpec | None = None,
) -> FlatFileCheckRunner:
    """Build a runner from a flat-file `Connection`'s primitives (secret resolved
    eagerly; raw config, not the ORM model). ``sampling=None`` = whole-object read.
    """
    if conn_type not in _FILE_TYPES:
        raise ValueError(f"{conn_type!r} is not a flat-file datasource")
    if not secret_ref:
        raise ValueError("flat-file connection requires secret_ref for the credential")
    secret = secret_store.get(secret_ref)
    return FlatFileCheckRunner(conn_type=conn_type, config=config, secret=secret, sampling=sampling)


# ───────────────────────── batch resolution ────────────────────────
# Batch pattern = regex whose FIRST capture group is the batch key; `latest`
# takes the greatest key, `specific` a named one. Resolution is pure; only the
# object listing is a live seam.


class BatchNotFoundError(ValueError):
    """No file matched the batch pattern (or the requested specific batch)."""


@dataclass(frozen=True)
class FileRef:
    """A listed object: its full key/blob path and last-modified time (if any)."""

    path: str
    last_modified: datetime | None = None


class BatchListingTooLargeError(ValueError):
    """The batch prefix lists more objects than resolution will scan (#943)."""


def _most_recent(files: list[FileRef]) -> str:
    """Path of the most recently modified file (ties broken by path; `files` non-empty)."""
    return max(files, key=lambda f: (f.last_modified or _MIN_DT, f.path)).path


def _rank(file: FileRef) -> tuple[datetime, str]:
    """Recency ordering key — the streaming equivalent of `_most_recent`'s."""
    return (file.last_modified or _MIN_DT, file.path)


def resolve_batch(
    files: Iterable[FileRef], *, pattern: str, strategy: str = "latest", batch: str | None = None
) -> str:
    """Pick one file's path from `files` per the batch `pattern` + `strategy`."""
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
        # An optional group that didn't participate (`None`) has no key — falls
        # to modified-time ordering rather than comparing None vs str.
        key = match.group(1) if match.groups() else None
        rank = _rank(file)
        if key is not None and (best_keyed is None or key > best_keyed[0]):
            best_keyed = (key, file.path)
        if best_recent is None or rank > best_recent:
            best_recent = rank
        if key is not None and key == batch and (best_of_batch is None or rank > best_of_batch):
            best_of_batch = rank

    # Order preserved: "nothing matched" reports before an invalid strategy.
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
    """Stream objects/blobs under `prefix` (live seam, generator — #943)."""
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
    """The whole listing under `prefix` as a list (live seam); batch resolution
    uses `iter_files` instead (#943).
    """
    return list(iter_files(conn_type=conn_type, config=config, prefix=prefix, secret=secret))


#: Soft limit makes the listing cost visible (#839); hard limit refuses — a
#: partial scan can't answer, since ascending listings put the newest key last.
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
    """Stream the listing under `prefix` and resolve the batch file path (#943)."""
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
        # Only when large but answered — a refused listing already logged its error.
        if _BATCH_LISTING_WARN_AT <= scanned < _BATCH_LISTING_MAX:
            log.warning("flatfile_batch_listing_large", scanned=scanned, conn_type=conn_type)


# ── preview-time listing budget (#1243) ─────────────────────────────
# `resolve_batch_file` above backs the RUN path (Celery worker) and keeps
# `_BATCH_LISTING_MAX` untouched. The preview endpoint runs synchronously in the
# API process's threadpool, so it needs its own, much smaller budget on both
# object count AND wall clock — an author typing into a broad prefix must not be
# able to pin an API thread. When the budget runs out first, the scan stops
# without raising: a preview is a hint, so "here's what the first N objects
# said" is an honest, useful answer (mirrors the #1105 asset-truncation shape),
# not an error.


@dataclass
class BatchPreviewResult:
    """Outcome of a budget-bounded preview scan. ``truncated`` means the object
    or wall-clock budget was hit before the listing was exhausted — ``path`` is
    then the best match seen so far (or ``None``), never a guarantee that a
    later, unscanned object wouldn't have won instead.
    """

    path: str | None
    scanned: int
    truncated: bool


@dataclass
class _Budget:
    """Mutable counters the bounded generator updates as it runs, read back by
    the caller once iteration stops (a generator can't return extra values).
    """

    max_objects: int
    max_seconds: float
    scanned: int = 0
    truncated: bool = False


def _budget_bounded(files: Iterable[FileRef], budget: _Budget) -> Iterator[FileRef]:
    """Yield from `files`, stopping (without raising) once either budget is hit."""
    deadline = time.monotonic() + budget.max_seconds
    for file in files:
        if budget.scanned >= budget.max_objects or time.monotonic() >= deadline:
            budget.truncated = True
            return
        budget.scanned += 1
        yield file


def resolve_batch_file_preview(
    *,
    conn_type: str,
    config: dict[str, Any],
    secret: str,
    prefix: str,
    pattern: str,
    strategy: str = "latest",
    batch: str | None = None,
    max_objects: int,
    max_seconds: float,
) -> BatchPreviewResult:
    """Preview-only counterpart to `resolve_batch_file`: bounded scan, honest
    partial answer instead of `BatchListingTooLargeError` (#1243).
    """
    budget = _Budget(max_objects=max_objects, max_seconds=max_seconds)
    stream = iter_files(conn_type=conn_type, config=config, prefix=prefix, secret=secret)
    with closing(stream):
        try:
            path = resolve_batch(
                _budget_bounded(stream, budget), pattern=pattern, strategy=strategy, batch=batch
            )
        except BatchNotFoundError:
            path = None
    return BatchPreviewResult(path=path, scanned=budget.scanned, truncated=budget.truncated)
