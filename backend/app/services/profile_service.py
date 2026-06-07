"""Column profiler — per-column statistics for the check editor.

Given a target (a SQL table or a flat file) and a set of columns on a suite's
connection, compute the stats an author needs before writing expectations: row
count, null count / fraction, distinct count, min / max, and the most frequent
values. Persists nothing — a read-only authoring aid (the check-editor "profile
on table/file select" panel).

`profile_connection` dispatches on the connection type:

* **SQL datasources** (Snowflake + Unity Catalog) — aggregate the stats
  in-warehouse with one round-trip + a top-values query per column, via the
  datasource's SQLAlchemy dialect. Unity Catalog adds a `catalog` so the table is
  qualified `catalog.schema.table` (3-level namespace); Snowflake is `schema.table`.
* **Flat-file datasources** (ADLS Gen2, S3) — download a *sample* of the file
  (`_SAMPLE_ROWS` rows) into Pandas and compute the same stats locally. CSV and
  Parquet are supported; stats are therefore over the sample, not the whole file.

**SQL-injection safety.** For SQL datasources, table / schema / column names are
caller-supplied and become SQL *identifiers* (they can't be bound parameters).
Queries are built with the SQLAlchemy Core expression language (`select` /
`table` / `column`) — never string formatting — so the dialect does the quoting
and there is no raw-SQL sink. As defence-in-depth (and a clean early 422) each
identifier is also validated against a strict allowlist. Flat-file columns are
checked for existence against the loaded frame instead (a missing column is a
clean 422, and Pandas indexing never builds SQL).

Like the GX adapter, the pure pieces (identifier validation, query building,
dataframe profiling, result assembly) are unit-testable without a live
datasource; the I/O seams (`_open_connection`, `_read_dataframe`) are
monkeypatched in tests, and a live smoke is deferred.
"""

from __future__ import annotations

import io
import math
import re
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy import column, distinct, func, quoted_name, select, table
from sqlalchemy.sql import Select

from backend.app.core.errors import DataQError
from backend.app.core.jsonsafe import sanitize_json
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.datasources.flatfile import download_bytes, format_from_path
from backend.app.datasources.snowflake import SnowflakeConfig, build_connection_string
from backend.app.datasources.unity_catalog import UnityCatalogConfig, build_databricks_url
from backend.app.db.models import Connection

log = get_logger(__name__)

# Connection types the profiler can read, grouped by how it reads them.
_SQL_TYPES = {"snowflake", "unity_catalog"}
_FILE_TYPES = {"adls_gen2", "s3"}

_SUPPORTED_FORMATS = {"csv", "parquet"}
# Flat-file profiling reads at most this many rows — stats are over the sample.
_SAMPLE_ROWS = 100_000

# A plain SQL identifier: letter/underscore start, then letters/digits/_/$ (the
# Snowflake unquoted-identifier set). Anything else (spaces, quotes, dots, etc.)
# is refused — it can't be made injection-safe by quoting alone here.
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")

# Connector timeouts (seconds): fail fast rather than hang the request thread.
_LOGIN_TIMEOUT = 10
_NETWORK_TIMEOUT = 30


class ProfileUnsupportedError(DataQError):
    status_code = 422
    code = "profile_unsupported"


class ProfileTargetInvalidError(DataQError):
    status_code = 422
    code = "profile_target_invalid"


class ProfileIdentifierInvalidError(DataQError):
    status_code = 422
    code = "profile_identifier_invalid"


class ProfileColumnNotFoundError(DataQError):
    status_code = 422
    code = "profile_column_not_found"


class ProfileFailedError(DataQError):
    status_code = 502
    code = "profile_failed"


@dataclass(frozen=True)
class ColumnProfile:
    column: str
    null_count: int
    null_fraction: float
    distinct_count: int
    min_value: Any
    max_value: Any
    top_values: list[dict[str, Any]]  # [{"value": ..., "count": int}]


@dataclass(frozen=True)
class ProfileResult:
    """A profiled target. Identity fields are type-specific: SQL datasources set
    `table` / `schema`, flat-file datasources set `path` / `file_format`."""

    row_count: int
    columns: list[ColumnProfile]
    table: str | None = None
    schema: str | None = None
    catalog: str | None = None
    path: str | None = None
    file_format: str | None = None


# ───────────────────────── pure query builders ─────────────────────


def validate_identifier(name: str | None) -> str:
    """Validate `name` against the plain-identifier allowlist and return it.

    Raises `ProfileIdentifierInvalidError` (422) for anything that isn't a plain
    identifier. The SQLAlchemy Core builders quote safely on their own; this is
    defence-in-depth and turns an odd name into a clean 422 instead of a quoted
    column that simply doesn't exist.
    """
    if not name or not _IDENTIFIER.match(name):
        raise ProfileIdentifierInvalidError(
            "not a valid table/schema/column identifier", detail={"identifier": name}
        )
    return name


def _table(schema: str, table_name: str, catalog: str | None = None) -> Any:
    """A Core table clause, optionally with a 3-level namespace (Unity Catalog).

    With a `catalog`, the namespace is `catalog.schema` passed as an unquoted
    `quoted_name` so the dialect emits three dotted parts (`catalog.schema.table`)
    rather than quoting the dotted string as one identifier. Safe because every
    part is allowlist-validated.
    """
    validate_identifier(schema)
    if catalog is not None:
        validate_identifier(catalog)
        namespace: Any = quoted_name(f"{catalog}.{schema}", quote=False)
    else:
        namespace = schema
    return table(validate_identifier(table_name), schema=namespace)


def build_aggregate_query(
    schema: str, table_name: str, columns: list[str], catalog: str | None = None
) -> Select[Any]:
    """One round-trip: row count + null/distinct/min/max per column.

    Built with the Core expression language (no string SQL); identifiers are
    validated then handed to `column()`/`table()`, which the dialect quotes.
    """
    projection: list[Any] = [func.count().label("row_count")]
    for i, col in enumerate(columns):
        c: Any = column(validate_identifier(col))
        projection.append((func.count() - func.count(c)).label(f"nulls_{i}"))
        projection.append(func.count(distinct(c)).label(f"distinct_{i}"))
        projection.append(func.min(c).label(f"min_{i}"))
        projection.append(func.max(c).label(f"max_{i}"))
    return select(*projection).select_from(_table(schema, table_name, catalog))


def build_top_values_query(
    schema: str, table_name: str, col: str, top_n: int, catalog: str | None = None
) -> Select[Any]:
    """Most frequent non-null values for one column (highest count first)."""
    c: Any = column(validate_identifier(col))
    freq = func.count().label("freq")
    return (
        select(c.label("value"), freq)
        .select_from(_table(schema, table_name, catalog))
        .where(c.is_not(None))
        .group_by(c)
        .order_by(func.count().desc(), c)
        .limit(int(top_n))
    )


def assemble_profile(
    *,
    table: str,
    schema: str,
    columns: list[str],
    aggregate: Mapping[str, Any],
    top_values: dict[str, list[Mapping[str, Any]]],
    catalog: str | None = None,
) -> ProfileResult:
    """Build the `ProfileResult` from raw query rows (pure, warehouse-free)."""
    row_count = int(aggregate["row_count"])
    profiles: list[ColumnProfile] = []
    for i, col in enumerate(columns):
        nulls = int(aggregate[f"nulls_{i}"])
        profiles.append(
            ColumnProfile(
                column=col,
                null_count=nulls,
                null_fraction=(nulls / row_count) if row_count else 0.0,
                distinct_count=int(aggregate[f"distinct_{i}"]),
                min_value=sanitize_json(aggregate[f"min_{i}"]),
                max_value=sanitize_json(aggregate[f"max_{i}"]),
                top_values=[
                    {"value": sanitize_json(r["value"]), "count": int(r["freq"])}
                    for r in top_values.get(col, [])
                ],
            )
        )
    return ProfileResult(
        table=table, schema=schema, catalog=catalog, row_count=row_count, columns=profiles
    )


# ───────────────────────── I/O seam (monkeypatched in tests) ────────


def _engine_args(connection: Connection, secret: str) -> tuple[str, dict[str, Any]]:
    """Build the (SQLAlchemy URL, connect_args) for a SQL datasource connection."""
    if connection.type == "unity_catalog":
        cfg = UnityCatalogConfig.model_validate(connection.config)
        # Catalog is not pinned on the URL — the profiler query qualifies the full
        # catalog.schema.table namespace itself (see `_table`).
        return build_databricks_url(cfg, secret), {}
    sf = SnowflakeConfig.model_validate(connection.config)
    return build_connection_string(sf, secret), {
        "login_timeout": _LOGIN_TIMEOUT,
        "network_timeout": _NETWORK_TIMEOUT,
    }


@contextmanager
def _open_connection(connection: Connection, secret_store: SecretStore) -> Generator[Any]:
    """Yield a live SQLAlchemy connection to the datasource, disposing the engine."""
    from sqlalchemy import create_engine

    if not connection.secret_ref:
        raise ValueError("connection requires secret_ref for the credential")
    secret = secret_store.get(connection.secret_ref)
    url, connect_args = _engine_args(connection, secret)
    engine = create_engine(url, connect_args=connect_args)
    try:
        with engine.connect() as conn:
            yield conn
    finally:
        engine.dispose()


# ───────────────────────── orchestration ───────────────────────────


def profile_table(
    connection: Connection,
    *,
    table: str,
    schema: str | None,
    columns: list[str],
    top_n: int,
    secret_store: SecretStore,
    catalog: str | None = None,
) -> ProfileResult:
    """Profile `columns` of a SQL `table` on `connection` (dispatched here for
    SQL datasource types). `catalog` qualifies the namespace for Unity Catalog
    (`catalog.schema.table`); Snowflake leaves it `None`.

    Raises `ProfileIdentifierInvalidError` (422) for a bad catalog/schema/table/
    column name (validated *before* any query runs), and `ProfileFailedError`
    (502) if the profile can't execute — the adapter exception is never echoed
    (it can carry DSN/credential fragments).
    """
    effective_schema = schema if schema is not None else connection.config.get("schema")
    if not isinstance(effective_schema, str):
        raise ProfileIdentifierInvalidError(
            "no schema given and the connection has none", detail={"schema": effective_schema}
        )
    # Validate every identifier up front (422) before any query is built/run.
    if catalog is not None:
        validate_identifier(catalog)
    validate_identifier(table)
    validate_identifier(effective_schema)
    for col in columns:
        validate_identifier(col)

    try:
        with _open_connection(connection, secret_store) as conn:
            aggregate = (
                conn.execute(build_aggregate_query(effective_schema, table, columns, catalog))
                .mappings()
                .one()
            )
            top_values = {
                col: list(
                    conn.execute(
                        build_top_values_query(effective_schema, table, col, top_n, catalog)
                    ).mappings()
                )
                for col in columns
            }
    except Exception as exc:
        log.warning(
            "column_profile_failed", connection_type=connection.type, error_type=type(exc).__name__
        )
        raise ProfileFailedError(
            "column profile could not execute against the datasource", detail={"table": table}
        ) from exc

    return assemble_profile(
        table=table,
        schema=effective_schema,
        catalog=catalog,
        columns=columns,
        aggregate=aggregate,
        top_values=top_values,
    )


# ───────────────────────── flat-file profiling ─────────────────────


def infer_file_format(path: str, explicit: str | None) -> str:
    """Resolve the file format from an explicit value or the path extension.

    Raises `ProfileTargetInvalidError` (422) for an unknown/unsupported format —
    the caller can always pass `file_format` to override extension guessing. The
    extension mapping is shared with the runner (`flatfile.format_from_path`).
    """
    fmt = explicit or format_from_path(path)
    if fmt not in _SUPPORTED_FORMATS:
        raise ProfileTargetInvalidError(
            "cannot determine a supported file format; pass file_format",
            detail={"path": path, "supported": sorted(_SUPPORTED_FORMATS)},
        )
    return fmt


def _to_native(value: Any) -> Any:
    """Coerce a numpy/pandas scalar to a JSON-friendly Python value."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):  # Timestamp / datetime / date
        return value.isoformat()
    if hasattr(value, "item"):  # numpy scalar → Python scalar
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def profile_dataframe(
    df: Any, *, columns: list[str], top_n: int, path: str, file_format: str
) -> ProfileResult:
    """Compute per-column stats from an in-memory dataframe (pure, no I/O).

    Raises `ProfileColumnNotFoundError` (422) if a requested column isn't in the
    frame — a clean error instead of a KeyError 500.
    """
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ProfileColumnNotFoundError(
            "requested column(s) not in the file",
            detail={"missing": missing, "available": [str(c) for c in df.columns][:50]},
        )
    row_count = len(df)
    profiles: list[ColumnProfile] = []
    for col in columns:
        series = df[col]
        null_count = int(series.isna().sum())
        non_null = series.dropna()
        counts = non_null.value_counts().head(top_n)
        profiles.append(
            ColumnProfile(
                column=col,
                null_count=null_count,
                null_fraction=(null_count / row_count) if row_count else 0.0,
                distinct_count=int(non_null.nunique()),
                min_value=sanitize_json(_to_native(non_null.min())) if len(non_null) else None,
                max_value=sanitize_json(_to_native(non_null.max())) if len(non_null) else None,
                top_values=[
                    {"value": sanitize_json(_to_native(value)), "count": int(count)}
                    for value, count in counts.items()
                ],
            )
        )
    return ProfileResult(path=path, file_format=file_format, row_count=row_count, columns=profiles)


def _read_dataframe(
    connection: Connection,
    *,
    path: str,
    file_format: str,
    columns: list[str],
    secret_store: SecretStore,
) -> Any:
    """Download `path` from the flat-file datasource into a sampled dataframe.

    The live I/O seam (download + parse) — monkeypatched in tests. Applies the two
    "load less data" levers from the pandas scaling guide:

    * **column projection** — only the requested `columns` are parsed (CSV
      `usecols`, Parquet `columns=`), so profiling 3 of 200 columns doesn't read
      all 200. Unknown names are simply not selected; `profile_dataframe` then
      reports genuinely-missing ones as a clean 422.
    * **row sampling** — at most `_SAMPLE_ROWS` rows (CSV pushes the cap into the
      parser; Parquet is sliced after the projected read).

    Not done (deliberate, for an authoring-time sampler): streaming/range reads —
    the whole object is still downloaded before parsing — and out-of-core engines
    (Dask). Both are future work if a profiling-cost problem actually shows up.
    """
    import pandas as pd

    if not connection.secret_ref:
        raise ValueError("connection requires secret_ref for the credential")
    secret = secret_store.get(connection.secret_ref)
    wanted = set(columns)
    raw = io.BytesIO(
        download_bytes(
            conn_type=connection.type, config=connection.config, path=path, secret=secret
        )
    )
    if file_format == "csv":
        return pd.read_csv(raw, nrows=_SAMPLE_ROWS, usecols=lambda name: name in wanted)

    import pyarrow.parquet as pq

    available = set(pq.ParquetFile(raw).schema.names)
    raw.seek(0)
    present = [c for c in columns if c in available]
    # Parquet is already Arrow on disk; dtype_backend="pyarrow" keeps the buffers
    # zero-copy instead of materialising a numpy copy. The stat helpers + the
    # _to_native coercion are Arrow-scalar-safe (min/max → Python int/str,
    # timestamps → Timestamp.isoformat, NA dropped before reductions).
    return pd.read_parquet(raw, columns=present, dtype_backend="pyarrow").head(_SAMPLE_ROWS)


def profile_file(
    connection: Connection,
    *,
    path: str,
    file_format: str | None,
    columns: list[str],
    top_n: int,
    secret_store: SecretStore,
) -> ProfileResult:
    """Profile `columns` of a flat file on `connection` (ADLS Gen2 / S3).

    Raises `ProfileTargetInvalidError` (422) for an unknown format,
    `ProfileColumnNotFoundError` (422) for a missing column, and
    `ProfileFailedError` (502) if the file can't be read — the underlying
    exception is never echoed (it can carry credential/endpoint fragments).
    """
    fmt = infer_file_format(path, file_format)
    try:
        df = _read_dataframe(
            connection, path=path, file_format=fmt, columns=columns, secret_store=secret_store
        )
    except Exception as exc:
        log.warning(
            "column_profile_failed", connection_type=connection.type, error_type=type(exc).__name__
        )
        raise ProfileFailedError(
            "column profile could not read the file", detail={"path": path}
        ) from exc

    return profile_dataframe(df, columns=columns, top_n=top_n, path=path, file_format=fmt)


def profile_connection(
    connection: Connection,
    *,
    columns: list[str],
    top_n: int,
    table: str | None = None,
    schema: str | None = None,
    catalog: str | None = None,
    path: str | None = None,
    file_format: str | None = None,
    secret_store: SecretStore,
) -> ProfileResult:
    """Dispatch to the SQL or flat-file profiler based on the connection type.

    Raises `ProfileUnsupportedError` (422) for a type with no profiler, and
    `ProfileTargetInvalidError` (422) if the target for that type is missing
    (a SQL type needs `table`; Unity Catalog also needs `catalog`; a flat-file
    type needs `path`).
    """
    if connection.type in _SQL_TYPES:
        if not table:
            raise ProfileTargetInvalidError(
                "table is required to profile a SQL datasource", detail={"type": connection.type}
            )
        if connection.type == "unity_catalog" and not catalog:
            raise ProfileTargetInvalidError(
                "catalog is required to profile a Unity Catalog table",
                detail={"type": connection.type},
            )
        return profile_table(
            connection,
            table=table,
            schema=schema,
            catalog=catalog,
            columns=columns,
            top_n=top_n,
            secret_store=secret_store,
        )
    if connection.type in _FILE_TYPES:
        if not path:
            raise ProfileTargetInvalidError(
                "path is required to profile a flat-file datasource",
                detail={"type": connection.type},
            )
        return profile_file(
            connection,
            path=path,
            file_format=file_format,
            columns=columns,
            top_n=top_n,
            secret_store=secret_store,
        )
    raise ProfileUnsupportedError(
        f"column profiling is not supported for {connection.type!r} connections in v1",
        detail={"type": connection.type, "supported": sorted(_SQL_TYPES | _FILE_TYPES)},
    )
