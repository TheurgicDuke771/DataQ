"""Flat-file (ADLS Gen2 / S3) IO + GX `CheckRunner`.

Two responsibilities for the flat-file datasources, both behind the same
primitives the SQL adapters use (raw config dict + resolved secret, never the ORM
row — keeps `datasources/` decoupled from `db/`):

* **IO** — `download_bytes` fetches an object/blob; `read_dataframe` parses it
  into pandas. Shared by the column profiler (service layer) and the runner.
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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar

import great_expectations as gx

from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.datasources.adls import AdlsConfig
from backend.app.datasources.base import CheckOutcome, CheckSpec, MonitorSpec, SuiteOutcome
from backend.app.datasources.gx_runner import run_expectations
from backend.app.datasources.monitors import (
    FRESHNESS,
    VOLUME,
    MonitorConfigError,
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

    sep = sniff_delimiter(raw.getvalue()[:_SNIFF_BYTES])
    raw.seek(0)
    return pd.read_csv(raw, sep=sep, **kwargs)


def _s3_client(cfg: S3Config, secret: str) -> Any:
    """A boto3 S3 client for `cfg` with the standard fail-fast timeouts."""
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        region_name=cfg.region,
        aws_access_key_id=cfg.access_key_id,
        aws_secret_access_key=secret,
        config=Config(connect_timeout=_CONNECT_TIMEOUT, read_timeout=_READ_TIMEOUT),
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


class FlatFileReadError(RuntimeError):
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
    if parsed.empty:
        raise MonitorConfigError(f"freshness column {column!r} holds no parseable timestamps")
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

        def dataframe() -> Any:
            if not attempt:
                try:
                    attempt.append(
                        read_dataframe(
                            conn_type=self._conn_type,
                            config=self._config,
                            path=table,
                            secret=self._secret,
                        )
                    )
                except Exception as exc:
                    # Classified, never echoed: this message is persisted to
                    # `results` and rendered in the UI/alerts/MCP, and object-store
                    # auth errors have carried credentials in their text (#828).
                    log.warning(
                        "flatfile_monitor_read_failed",
                        connection_type=self._conn_type,
                        error_type=type(exc).__name__,
                    )
                    attempt.append(FlatFileReadError(f"could not read {table!r} from the store"))
            if isinstance(attempt[0], FlatFileReadError):
                raise attempt[0]
            return attempt[0]

        def scalar_for(spec: MonitorSpec) -> Any:
            if spec.kind == VOLUME:
                return len(dataframe())
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


def _most_recent(files: list[FileRef]) -> str:
    """Path of the most recently modified file (ties broken by path; `files` non-empty)."""
    return max(files, key=lambda f: (f.last_modified or _MIN_DT, f.path)).path


def resolve_batch(
    files: list[FileRef], *, pattern: str, strategy: str = "latest", batch: str | None = None
) -> str:
    """Pick one file's path from `files` per the batch `pattern` + `strategy`.

    `pattern` is a regex `re.search`-ed against each path; its first capture group
    (if any) is the batch key. `strategy`:

    * ``latest`` — the greatest batch key (lexicographic — ISO dates sort right),
      or, when the pattern has no capture group, the most recently modified file.
    * ``specific`` — the file whose batch key equals `batch` (required).

    Raises `BatchNotFoundError` (nothing matched / no such batch) or `ValueError`
    (bad strategy, or `specific` without `batch`).
    """
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"invalid batch pattern {pattern!r}: {exc}") from exc
    matches = [(f, m) for f in files if (m := compiled.search(f.path))]
    if not matches:
        raise BatchNotFoundError(f"no files matched batch pattern {pattern!r}")

    if strategy == "specific":
        if batch is None:
            raise ValueError("strategy 'specific' requires a batch key")
        hits = [f for f, m in matches if m.groups() and m.group(1) == batch]
        if not hits:
            raise BatchNotFoundError(f"no file for batch {batch!r} under pattern {pattern!r}")
        return _most_recent(hits)

    if strategy != "latest":
        raise ValueError(f"unknown batch strategy {strategy!r}")

    # A batch key is the first capture group when it *participated* in the match;
    # an optional group that didn't (`None`) has no key, so it falls through to the
    # modified-time ordering rather than crashing the `max` on a None vs str compare.
    keyed = [(f, m.group(1)) for f, m in matches if m.groups() and m.group(1) is not None]
    if keyed:
        return max(keyed, key=lambda fk: fk[1])[0].path
    return _most_recent([f for f, _ in matches])


def list_files(
    *, conn_type: str, config: dict[str, Any], prefix: str, secret: str
) -> list[FileRef]:
    """List objects/blobs under `prefix` on a flat-file datasource (live seam)."""
    if conn_type == "s3":
        cfg = S3Config.model_validate(config)
        paginator = _s3_client(cfg, secret).get_paginator("list_objects_v2")
        refs: list[FileRef] = []
        for page in paginator.paginate(Bucket=cfg.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                refs.append(FileRef(path=obj["Key"], last_modified=obj.get("LastModified")))
        return refs

    acfg = AdlsConfig.model_validate(config)
    client_az = _blob_service(acfg, secret)
    try:
        container = client_az.get_container_client(acfg.container)
        return [
            FileRef(path=blob.name, last_modified=getattr(blob, "last_modified", None))
            for blob in container.list_blobs(name_starts_with=prefix)
        ]
    finally:
        client_az.close()


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
    """List under `prefix`, then resolve the batch file path (list + `resolve_batch`)."""
    files = list_files(conn_type=conn_type, config=config, prefix=prefix, secret=secret)
    return resolve_batch(files, pattern=pattern, strategy=strategy, batch=batch)
