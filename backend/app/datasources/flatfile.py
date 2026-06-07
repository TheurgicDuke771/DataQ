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

import io
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import great_expectations as gx

from backend.app.core.secrets import SecretStore
from backend.app.datasources.adls import AdlsConfig
from backend.app.datasources.base import CheckSpec, SuiteOutcome
from backend.app.datasources.gx_runner import run_expectations
from backend.app.datasources.s3 import S3Config

# Connector timeouts (seconds): fail fast rather than hang the worker thread.
_CONNECT_TIMEOUT = 10
_READ_TIMEOUT = 60

_FILE_TYPES = {"adls_gen2", "s3"}

# Sort floor for files the store reports without a modified time.
_MIN_DT = datetime.min.replace(tzinfo=UTC)


def format_from_path(path: str) -> str | None:
    """Infer the file format from the path extension (`None` if unrecognised)."""
    lower = path.lower()
    if lower.endswith(".csv"):
        return "csv"
    if lower.endswith((".parquet", ".pq")):
        return "parquet"
    return None


def download_bytes(*, conn_type: str, config: dict[str, Any], path: str, secret: str) -> bytes:
    """Fetch the object/blob bytes from S3 or ADLS Gen2 (live seam).

    Takes the connection's `type` + raw `config` + resolved `secret` (the caller
    owns the SecretStore), so this module never touches the DB.
    """
    if conn_type == "s3":
        import boto3
        from botocore.config import Config

        cfg = S3Config.model_validate(config)
        client = boto3.client(
            "s3",
            region_name=cfg.region,
            aws_access_key_id=cfg.access_key_id,
            aws_secret_access_key=secret,
            config=Config(connect_timeout=_CONNECT_TIMEOUT, read_timeout=_READ_TIMEOUT),
        )
        body: bytes = client.get_object(Bucket=cfg.bucket, Key=path)["Body"].read()
        return body

    from azure.storage.blob import BlobServiceClient

    acfg = AdlsConfig.model_validate(config)
    client_az: Any = BlobServiceClient(account_url=acfg.account_url, credential=secret)
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
        return pd.read_csv(raw)
    return pd.read_parquet(raw, dtype_backend="pyarrow")


class FlatFileCheckRunner:
    """`CheckRunner` for flat files — loads the file into pandas, runs GX on it.

    Holds the resolved credential (like `SnowflakeCheckRunner` holds its
    connection string), so `run_checks` is self-contained. ``table`` is the file
    path; ``schema`` is ignored (flat files have no schema namespace).
    """

    def __init__(self, *, conn_type: str, config: dict[str, Any], secret: str) -> None:
        self._conn_type = conn_type
        self._config = config
        self._secret = secret

    def run_checks(
        self, *, table: str, schema: str | None, checks: list[CheckSpec]
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
    compiled = re.compile(pattern)
    matches = [(f, m) for f in files if (m := compiled.search(f.path))]
    if not matches:
        raise BatchNotFoundError(f"no files matched batch pattern {pattern!r}")

    if strategy == "specific":
        if batch is None:
            raise ValueError("strategy 'specific' requires a batch key")
        hits = [f for f, m in matches if m.groups() and m.group(1) == batch]
        if not hits:
            raise BatchNotFoundError(f"no file for batch {batch!r} under pattern {pattern!r}")
        return max(hits, key=lambda f: (f.last_modified or _MIN_DT, f.path)).path

    if strategy != "latest":
        raise ValueError(f"unknown batch strategy {strategy!r}")

    keyed = [(f, m.group(1)) for f, m in matches if m.groups()]
    if keyed:
        return max(keyed, key=lambda fk: fk[1])[0].path
    # No capture group → fall back to the storage modified time.
    return max((f for f, _ in matches), key=lambda f: (f.last_modified or _MIN_DT, f.path)).path


def list_files(
    *, conn_type: str, config: dict[str, Any], prefix: str, secret: str
) -> list[FileRef]:
    """List objects/blobs under `prefix` on a flat-file datasource (live seam)."""
    if conn_type == "s3":
        import boto3
        from botocore.config import Config

        cfg = S3Config.model_validate(config)
        client = boto3.client(
            "s3",
            region_name=cfg.region,
            aws_access_key_id=cfg.access_key_id,
            aws_secret_access_key=secret,
            config=Config(connect_timeout=_CONNECT_TIMEOUT, read_timeout=_READ_TIMEOUT),
        )
        paginator = client.get_paginator("list_objects_v2")
        refs: list[FileRef] = []
        for page in paginator.paginate(Bucket=cfg.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                refs.append(FileRef(path=obj["Key"], last_modified=obj.get("LastModified")))
        return refs

    from azure.storage.blob import BlobServiceClient

    acfg = AdlsConfig.model_validate(config)
    client_az: Any = BlobServiceClient(account_url=acfg.account_url, credential=secret)
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
