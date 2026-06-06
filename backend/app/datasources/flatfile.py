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
