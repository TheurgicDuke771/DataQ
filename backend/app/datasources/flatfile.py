"""Flat-file (ADLS Gen2 / S3) IO + GX `CheckRunner`."""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Callable, Generator, Iterable, Iterator
from contextlib import closing
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


def download_bytes(*, conn_type: str, config: dict[str, Any], path: str, secret: str) -> bytes:
    """Fetch the object/blob bytes from S3 or ADLS Gen2 (live seam)."""
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
    """``length`` bytes of ``path`` from offset ``start`` (live seam, #882)."""
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
        #: Range requests issued — asserted by tests to verify the cheap path.
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
        # Clamp so a read near the end never asks past EOF.
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
    """Row count from the Parquet footer — a couple of range GETs, no data read (#942)."""
    import pyarrow.parquet as pq

    reader = RangeReader(conn_type=conn_type, config=config, path=path, secret=secret)
    rows: int = pq.ParquetFile(reader).metadata.num_rows
    return rows


def csv_row_count(*, conn_type: str, config: dict[str, Any], path: str, secret: str) -> int:
    """Row count of a CSV, streamed in batches — never a full DataFrame (#942)."""
    import pyarrow.csv as pv

    # Big window: this walks end to end; the seeking default would mean
    # thousands of range requests.
    reader = RangeReader(
        conn_type=conn_type, config=config, path=path, secret=secret, chunk=STREAM_CHUNK
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
    """Parse the first ``rows`` data rows of a CSV from a bounded head read (#882)."""
    # One byte MORE than the window, so a short read unambiguously means EOF —
    # a file of exactly window size must not be mistaken for a cut one.
    head = read_range(
        conn_type=conn_type,
        config=config,
        path=path,
        secret=secret,
        start=0,
        length=_CSV_HEAD_BYTES + 1,
    )
    if len(head) > _CSV_HEAD_BYTES:
        head = trim_to_row_boundary(head)
    return read_csv_bytes(io.BytesIO(head), nrows=rows)


def row_count(*, conn_type: str, config: dict[str, Any], path: str, secret: str) -> int:
    """Rows in a flat file by the cheapest route: Parquet footer or CSV stream (#942)."""
    fmt = format_from_path(path)
    if fmt is None:
        raise ValueError(f"unsupported flat-file format for path {path!r}")
    if fmt == "csv":
        return csv_row_count(conn_type=conn_type, config=config, path=path, secret=secret)
    return parquet_row_count(conn_type=conn_type, config=config, path=path, secret=secret)


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
    if fmt == "csv":
        import pyarrow.csv as pv

        sep = sniff_delimiter(read_range(**reader_args, start=0, length=_SNIFF_BYTES))
        stream = pv.open_csv(
            RangeReader(**reader_args, chunk=STREAM_CHUNK),
            parse_options=pv.ParseOptions(delimiter=sep),
        )
        return stream, stream.schema, False, stream.close

    import pyarrow.parquet as pq

    parquet = pq.ParquetFile(RangeReader(**reader_args, chunk=STREAM_CHUNK))
    return (
        parquet.iter_batches(batch_size=_SAMPLE_BATCH_ROWS),
        parquet.schema_arrow,
        True,
        parquet.close,
    )


def _csv_head_frame(reader_args: dict[str, Any], *, limit: int) -> tuple[Any, bool]:
    """The first ``limit`` rows of a CSV via a doubling byte range (#595)."""
    size = object_size(**reader_args)
    window = _CSV_HEAD_BYTES
    while True:
        span = min(window, size)
        raw = read_range(**reader_args, start=0, length=span)
        reached_eof = span >= size
        if not reached_eof:
            raw = trim_to_row_boundary(raw)
        frame = read_csv_bytes(io.BytesIO(raw), nrows=limit)
        if len(frame) >= limit or reached_eof:
            return frame, reached_eof
        window *= 2


def read_sampled_dataframe(
    *, conn_type: str, config: dict[str, Any], path: str, secret: str, sample: SampleSpec
) -> tuple[Any, dict[str, Any]]:
    """A bounded sample of a flat file, plus the record of what was sampled (#595)."""
    fmt = format_from_path(path)
    if fmt is None:
        raise ValueError(f"unsupported flat-file format for path {path!r}")
    reader_args: dict[str, Any] = {
        "conn_type": conn_type,
        "config": config,
        "path": path,
        "secret": secret,
    }

    if sample.strategy == SAMPLE_HEAD and fmt == "csv":
        frame, reached_eof = _csv_head_frame(reader_args, limit=sample.rows + 1)
        truncated = len(frame) > sample.rows
        if truncated:
            frame = frame.head(sample.rows)
        # Not truncated implies the range reached EOF, so the size is known free.
        csv_total = None if truncated else (len(frame) if reached_eof else None)
        return frame, sampling_record(
            sample, rows=len(frame), total_rows=csv_total, sampled=truncated
        )

    want_head = sample.strategy == SAMPLE_HEAD
    total: int | None = None
    indices: list[int] | None = None
    if not want_head:
        total = row_count(**reader_args)
        # `None` = sample covers the whole dataset — read straight through rather
        # than materialise an identity index list (~40 MB at 1.4M rows).
        indices = sample_row_indices(total=total, rows=sample.rows, seed=sample.seed)

    batches, schema, arrow_backed, close = _open_batch_stream(reader_args, fmt)
    try:
        if indices is not None:
            taken = take_indices(batches, indices)
        else:
            # `rows + 1` for head (probe row); the counted total for an
            # everything-covering random sample.
            taken = take_head(batches, limit=sample.rows + 1 if want_head else (total or 0))
    finally:
        close()

    read_rows = sum(batch.num_rows for batch in taken)
    if indices is not None:
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


def file_stat(*, conn_type: str, config: dict[str, Any], path: str, secret: str) -> FileStat:
    """The store's metadata for exactly ``path`` (live seam, #520/#595)."""
    if conn_type == "s3":
        from botocore.exceptions import ClientError

        cfg = S3Config.model_validate(config)
        try:
            head = _s3_client(cfg, secret).head_object(Bucket=cfg.bucket, Key=path)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return FileStat()
            raise
        return FileStat(last_modified=head.get("LastModified"), size=head.get("ContentLength"))

    from azure.core.exceptions import ResourceNotFoundError

    acfg = AdlsConfig.model_validate(config)
    client_az = _blob_service(acfg, secret)
    try:
        properties = client_az.get_blob_client(
            container=acfg.container, blob=path
        ).get_blob_properties()
        return FileStat(last_modified=properties.last_modified, size=properties.size)
    except ResourceNotFoundError:
        return FileStat()
    finally:
        client_az.close()


def file_last_modified(
    *, conn_type: str, config: dict[str, Any], path: str, secret: str
) -> datetime | None:
    """Just the arrival time from `file_stat` — the pre-#595 shape of this seam."""
    return file_stat(conn_type=conn_type, config=config, path=path, secret=secret).last_modified


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
        #: Per-path metadata memo (#595) — the guard can be reached twice per run.
        self._stats: dict[str, FileStat] = {}

    def _stat(self, path: str) -> FileStat:
        """`file_stat` for ``path``, once per runner instance."""
        stat = self._stats.get(path)
        if stat is None:
            stat = file_stat(
                conn_type=self._conn_type, config=self._config, path=path, secret=self._secret
            )
            self._stats[path] = stat
        return stat

    def _guard_object_size(self, path: str, *, stat: FileStat | None = None) -> None:
        """Refuse a full-object read exceeding ``RUN_MAX_SCAN_BYTES`` (#595)."""
        cap = get_settings().run_max_scan_bytes
        if cap <= 0:
            return
        size = (stat or self._stat(path)).size
        if size is not None:
            enforce_byte_cap(size, cap=cap, target=f"file {path!r}")

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
            self._guard_object_size(table, stat=stat)
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
