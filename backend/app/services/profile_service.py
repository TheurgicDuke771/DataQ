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
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy import column, distinct, func, literal_column, quoted_name, select, table
from sqlalchemy.sql import Select

from backend.app.core.errors import DataQError
from backend.app.core.jsonsafe import sanitize_json
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.datasources.flatfile import download_bytes, format_from_path
from backend.app.datasources.iceberg import IcebergConfig, read_iceberg_dataframe
from backend.app.datasources.iceberg import list_iceberg_columns as iceberg_column_names
from backend.app.datasources.snowflake import (
    SnowflakeConfig,
    build_connect_args,
    build_connection_string,
)
from backend.app.datasources.unity_catalog import UnityCatalogConfig, build_databricks_url
from backend.app.db.models import Connection
from backend.app.services.column_classification import ColumnClass, classify_column

log = get_logger(__name__)

# Formats the profiler can actually parse. NOT redundant with
# flatfile.format_from_path (which only recognises path extensions): this also
# validates the caller's `explicit` file_format override (an arbitrary string),
# and is deliberately a *subset* of recognised formats — a format can be
# recognised by path yet unsupported here, which should still 422 (#147).
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
    distinct_count: int | None  # None when the column's values aren't hashable
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


# ───────────────────────── shared stat contract ────────────────────


def null_fraction(null_count: int, row_count: int) -> float:
    """Fraction of rows that are null, guarding the empty-target divide-by-zero.

    The one stat definition the SQL profiler (`assemble_profile`) and the pandas
    profiler (`profile_dataframe`) can actually share — both must agree that a
    0-row target reports `0.0`, not `1.0` or a `ZeroDivisionError` (#147). The
    other contract points (distinct excludes nulls; top values are non-null,
    highest-count-first) are structurally SQL-vs-pandas and can't share code, so
    they're pinned by the parallel-path tests instead.
    """
    return (null_count / row_count) if row_count else 0.0


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


def build_columns_query(schema: str, table_name: str, catalog: str | None = None) -> Select[Any]:
    """List a target's column names: `SELECT * FROM <target> LIMIT 0`.

    Returns no rows, but the cursor still exposes the column names via
    `result.keys()` — so it's a cheap, dialect-agnostic way to introspect columns
    that reuses the same catalog-aware, allowlist-validated `_table` namespace as
    the profiler (rather than the SQLAlchemy inspector, which is fiddly for Unity
    Catalog's 3-level `catalog.schema.table`). `literal_column("*")` is a SQL
    constant, not caller input — the only caller-supplied parts go through
    `_table`'s identifier validation.
    """
    return select(literal_column("*")).select_from(_table(schema, table_name, catalog)).limit(0)


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
                null_fraction=null_fraction(nulls, row_count),
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


# ───────────────────────── profiler registry ───────────────────────
#
# One table maps connection.type to its profiling strategy, so adding a
# datasource is a single entry here — not edits scattered across the type sets,
# `_engine_args`, and `profile_connection` (#146). SQL types carry their engine
# builder + whether they need a `catalog`; flat-file types are uniform (the
# object-store backend, S3 vs ADLS, is dispatched inside `flatfile`).


def _snowflake_engine_args(connection: Connection, secret: str) -> tuple[str, dict[str, Any]]:
    sf = SnowflakeConfig.model_validate(connection.config)
    return build_connection_string(sf, secret), {
        "login_timeout": _LOGIN_TIMEOUT,
        "network_timeout": _NETWORK_TIMEOUT,
        # Key-pair auth threads the private key in as a connect-arg (empty for password).
        **build_connect_args(sf, secret),
    }


def _unity_catalog_engine_args(connection: Connection, secret: str) -> tuple[str, dict[str, Any]]:
    cfg = UnityCatalogConfig.model_validate(connection.config)
    # Catalog is not pinned on the URL — the profiler query qualifies the full
    # catalog.schema.table namespace itself (see `_table`).
    return build_databricks_url(cfg, secret), {}


@dataclass(frozen=True)
class _SqlProfiler:
    """SQL profiling strategy: in-warehouse aggregation over a SQLAlchemy engine."""

    engine_args: Callable[[Connection, str], tuple[str, dict[str, Any]]]
    requires_catalog: bool = False


@dataclass(frozen=True)
class _FileProfiler:
    """Flat-file profiling strategy: sample into pandas (backend handled by flatfile)."""


@dataclass(frozen=True)
class _IcebergProfiler:
    """Iceberg profiling strategy: native ``pyiceberg`` read into pandas (ADR 0030).

    NOT a `_SqlProfiler`: the Iceberg identifier is ``namespace.table`` (dotted),
    which the SQL path's `validate_identifier` rejects, and there is no SQL engine —
    the table is materialised and profiled in-pandas like the flat-file path. The
    credential is **optional** (a local warehouse / vended-credentials REST catalog
    has none), so this type is exempt from the `secret_ref` guard in
    `resolve_profiler`, mirroring `build_iceberg_runner`."""


_Profiler = _SqlProfiler | _FileProfiler | _IcebergProfiler

_PROFILERS: dict[str, _Profiler] = {
    "snowflake": _SqlProfiler(_snowflake_engine_args),
    "unity_catalog": _SqlProfiler(_unity_catalog_engine_args, requires_catalog=True),
    "s3": _FileProfiler(),
    "adls_gen2": _FileProfiler(),
    "iceberg": _IcebergProfiler(),
}


# ───────────────────────── I/O seam (monkeypatched in tests) ────────


def _engine_args(connection: Connection, secret: str) -> tuple[str, dict[str, Any]]:
    """Build the (SQLAlchemy URL, connect_args) for a SQL datasource connection."""
    profiler = _PROFILERS.get(connection.type)
    if not isinstance(profiler, _SqlProfiler):
        raise ProfileUnsupportedError(
            f"{connection.type!r} is not a SQL profiling datasource",
            detail={"type": connection.type},
        )
    return profiler.engine_args(connection, secret)


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


def resolve_profiler(
    connection: Connection,
    *,
    table: str | None,
    catalog: str | None,
    path: str | None,
) -> _Profiler:
    """Validate that `connection` is profilable and its target is well-formed,
    returning the matched profiler strategy.

    The one target-validation rule set shared by the profiler (`profile_connection`)
    and the column lister (`list_columns`) so they can't drift: a type with no
    profiler → `ProfileUnsupportedError` (422); a missing credential or a missing
    target for that type (SQL needs `table`; Unity Catalog also needs `catalog`;
    a flat-file type needs `path`) → `ProfileTargetInvalidError` (422). The
    no-credential check is here (not left to the adapter) so it surfaces as a
    clean 422 rather than a bare `ValueError` the connect guard would relabel 502.
    """
    profiler = _PROFILERS.get(connection.type)
    if profiler is None:
        raise ProfileUnsupportedError(
            f"column introspection is not supported for {connection.type!r} connections in v1",
            detail={"type": connection.type, "supported": sorted(_PROFILERS)},
        )
    # Iceberg is credential-optional (like `build_iceberg_runner` / the ADLS/S3
    # adapters) — a local warehouse or vended-credentials REST catalog has no
    # secret. Every other type still requires a stored credential, surfaced as a
    # clean 422 rather than a bare ValueError the connect guard would relabel 502.
    if not isinstance(profiler, _IcebergProfiler) and not connection.secret_ref:
        raise ProfileTargetInvalidError(
            "connection has no stored credential (secret_ref)", detail={"type": connection.type}
        )
    if isinstance(profiler, _IcebergProfiler):
        if not table:
            raise ProfileTargetInvalidError(
                "table is required for an Iceberg table", detail={"type": connection.type}
            )
    elif isinstance(profiler, _SqlProfiler):
        if not table:
            raise ProfileTargetInvalidError(
                "table is required for a SQL datasource", detail={"type": connection.type}
            )
        if profiler.requires_catalog and not catalog:
            raise ProfileTargetInvalidError(
                "catalog is required for a Unity Catalog table", detail={"type": connection.type}
            )
    elif not path:
        raise ProfileTargetInvalidError(
            "path is required for a flat-file datasource", detail={"type": connection.type}
        )
    return profiler


def resolve_effective_schema(connection: Connection, schema: str | None) -> str:
    """The schema to qualify a SQL target with: the explicit `schema`, else the
    connection's configured default. Raises `ProfileIdentifierInvalidError` (422)
    when neither is set. Shared by `profile_table` and `list_table_columns`."""
    effective_schema = schema if schema is not None else connection.config.get("schema")
    if not isinstance(effective_schema, str):
        raise ProfileIdentifierInvalidError(
            "no schema given and the connection has none", detail={"schema": effective_schema}
        )
    return effective_schema


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
    effective_schema = resolve_effective_schema(connection, schema)
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
    if isinstance(value, bool | int | float | str):
        return value
    # Anything else a column can hold (bytes/binary, Decimal, UUID, …) → a display
    # string, so a min/max/top value is always JSON-encodable, never a 500 at the
    # response boundary. `bool` is matched above `int` since bool is an int.
    return str(value)


def _profile_columns(df: Any, *, columns: list[str], top_n: int) -> tuple[int, list[ColumnProfile]]:
    """Row count + per-column stats for `columns` of an in-memory dataframe.

    The datasource-neutral core of the pandas profiling path, shared by the
    flat-file (`profile_dataframe`) and Iceberg (`profile_iceberg`) profilers so
    they can't drift on the stats contract — only the `ProfileResult` identity
    fields (`path`/`file_format` vs `table`) differ per datasource. Raises
    `ProfileColumnNotFoundError` (422) if a requested column isn't in the frame —
    a clean error instead of a KeyError 500.
    """
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ProfileColumnNotFoundError(
            "requested column(s) not in the target",
            detail={"missing": missing, "available": [str(c) for c in df.columns][:50]},
        )
    row_count = len(df)
    profiles = [_profile_series(col, df[col], row_count=row_count, top_n=top_n) for col in columns]
    return row_count, profiles


def profile_dataframe(
    df: Any, *, columns: list[str], top_n: int, path: str, file_format: str
) -> ProfileResult:
    """Compute per-column stats from an in-memory dataframe (pure, no I/O).

    Raises `ProfileColumnNotFoundError` (422) if a requested column isn't in the
    frame — a clean error instead of a KeyError 500.
    """
    row_count, profiles = _profile_columns(df, columns=columns, top_n=top_n)
    return ProfileResult(path=path, file_format=file_format, row_count=row_count, columns=profiles)


def _profile_series(column: str, series: Any, *, row_count: int, top_n: int) -> ColumnProfile:
    """Per-column stats, degrading a messy column to nulls instead of 500-ing.

    `null_count` is always computable, but a real-world flat file can hold a
    column the stats can't process: min/max raise on **uncomparable** mixed types
    (e.g. ints and strings in one object column), and distinct/value_counts raise
    on **unhashable** cells (nested list/dict values from Parquet). Each best-effort
    stat is guarded independently — and broadly, since the exception type varies by
    backend (a numpy object column raises `TypeError`, a pyarrow-backed Parquet
    list/struct column raises `ArrowNotImplementedError`) — so one bad column yields
    null stats for itself rather than failing the whole profile request.
    """
    null_count = int(series.isna().sum())
    non_null = series.dropna()
    try:
        minimum = _to_native(non_null.min()) if len(non_null) else None
        maximum = _to_native(non_null.max()) if len(non_null) else None
    except Exception:
        minimum = maximum = None
    try:
        distinct: int | None = int(non_null.nunique())
    except Exception:
        distinct = None
    try:
        counts = non_null.value_counts().head(top_n)
        top = [
            {"value": sanitize_json(_to_native(value)), "count": int(count)}
            for value, count in counts.items()
        ]
    except Exception:
        top = []
    return ColumnProfile(
        column=column,
        null_count=null_count,
        null_fraction=null_fraction(null_count, row_count),
        distinct_count=distinct,
        min_value=sanitize_json(minimum),
        max_value=sanitize_json(maximum),
        top_values=top,
    )


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


# ───────────────────────── Iceberg profiling (native read) ─────────


def _iceberg_identifier(table: str, namespace: str | None) -> str:
    """Fold the optional `namespace` into the ``namespace.table`` identifier
    ``pyiceberg`` addresses a table by — mirroring `run_target.resolve_target`'s
    Iceberg branch, so the profiler and the run path resolve the same table."""
    return f"{namespace}.{table}" if namespace else table


def _read_iceberg_dataframe(
    connection: Connection, *, identifier: str, columns: list[str], secret_store: SecretStore
) -> Any:
    """Resolve an Iceberg connection's config + optional secret (exactly as
    `build_iceberg_runner` does — the credential is optional) and materialise the
    target as a projected, sampled DataFrame.

    The live I/O seam (catalog load + scan), monkeypatched in tests — the
    Iceberg analogue of the flat-file profiler's `_read_dataframe`."""
    config = IcebergConfig.model_validate(connection.config)
    secret = secret_store.get(connection.secret_ref) if connection.secret_ref else None
    return read_iceberg_dataframe(config, secret, identifier, columns=columns, limit=_SAMPLE_ROWS)


def _list_iceberg_columns(
    connection: Connection, *, identifier: str, secret_store: SecretStore
) -> list[str]:
    """Resolve config + optional secret and list the target's schema field names
    (metadata only, no data scan) — the Iceberg column-listing I/O seam."""
    config = IcebergConfig.model_validate(connection.config)
    secret = secret_store.get(connection.secret_ref) if connection.secret_ref else None
    return iceberg_column_names(config, secret, identifier)


def profile_iceberg(
    connection: Connection,
    *,
    table: str,
    namespace: str | None,
    columns: list[str],
    top_n: int,
    secret_store: SecretStore,
) -> ProfileResult:
    """Profile `columns` of a natively-read Iceberg `table` on `connection` (#721).

    Materialises a projected, sampled DataFrame via ``pyiceberg`` and reuses the
    shared pandas profiling core — the Iceberg identifier is ``namespace.table``,
    which the SQL path can't handle, so this never routes through `profile_table`.
    Raises `ProfileColumnNotFoundError` (422) for a missing column and
    `ProfileFailedError` (502) if the table can't be read — the underlying
    exception is never echoed (it can carry catalog/credential fragments).
    """
    identifier = _iceberg_identifier(table, namespace)
    try:
        df = _read_iceberg_dataframe(
            connection, identifier=identifier, columns=columns, secret_store=secret_store
        )
    except Exception as exc:
        log.warning(
            "column_profile_failed", connection_type=connection.type, error_type=type(exc).__name__
        )
        raise ProfileFailedError(
            "column profile could not read the Iceberg table", detail={"table": identifier}
        ) from exc

    row_count, profiles = _profile_columns(df, columns=columns, top_n=top_n)
    return ProfileResult(table=identifier, row_count=row_count, columns=profiles)


def list_iceberg_columns(
    connection: Connection,
    *,
    table: str,
    namespace: str | None,
    secret_store: SecretStore,
) -> list[str]:
    """Column (field) names of an Iceberg `table` on `connection` — no data scan.

    Reads the table's schema field names (metadata only). Raises
    `ProfileFailedError` (502) if the table can't be read (exception not echoed).
    """
    identifier = _iceberg_identifier(table, namespace)
    try:
        return _list_iceberg_columns(connection, identifier=identifier, secret_store=secret_store)
    except Exception as exc:
        log.warning(
            "column_list_failed", connection_type=connection.type, error_type=type(exc).__name__
        )
        raise ProfileFailedError(
            "columns could not be listed from the Iceberg table", detail={"table": identifier}
        ) from exc


def derive_column_policy(columns: list[ColumnProfile]) -> dict[str, Any]:
    """Auto-derive a failing-sample redaction policy (#415) from a column profile.

    Classifies each column by name + its sampled top-values and returns the
    ``{identifier_column, pii_columns}`` shape stored on ``Suite.column_policy``:

    * ``pii_columns`` — every column the classifier flags PII (masked in samples);
    * ``identifier_column`` — the best row locator: the highest-cardinality column
      classified IDENTIFIER (most unique → most useful to pinpoint a failing row),
      ties broken by name. Omitted when no column looks like an identifier.

    A convenience the author reviews and can override — the *stored* policy is
    authoritative, and the datasource-tag layer (level 1) still overrules for masking.
    """
    pii: list[str] = []
    identifiers: list[tuple[int, str]] = []  # (distinct_count, name) → pick the most unique
    for col in columns:
        values = [tv.get("value") for tv in col.top_values]
        cls = classify_column(col.column, values)
        if cls is ColumnClass.PII:
            pii.append(col.column)
        elif cls is ColumnClass.IDENTIFIER:
            identifiers.append((col.distinct_count or 0, col.column))
    policy: dict[str, Any] = {"pii_columns": pii}
    if identifiers:
        identifiers.sort(key=lambda item: (-item[0], item[1]))
        policy["identifier_column"] = identifiers[0][1]
    return policy


def profile_connection(
    connection: Connection,
    *,
    columns: list[str],
    top_n: int,
    table: str | None = None,
    schema: str | None = None,
    catalog: str | None = None,
    namespace: str | None = None,
    path: str | None = None,
    file_format: str | None = None,
    secret_store: SecretStore,
) -> ProfileResult:
    """Dispatch to the SQL, flat-file, or Iceberg profiler based on the type.

    Raises `ProfileUnsupportedError` (422) for a type with no profiler, and
    `ProfileTargetInvalidError` (422) if the target for that type is missing
    (a SQL/Iceberg type needs `table`; Unity Catalog also needs `catalog`; a
    flat-file type needs `path`) or a credential-requiring connection has none.
    """
    profiler = resolve_profiler(connection, table=table, catalog=catalog, path=path)
    if isinstance(profiler, _IcebergProfiler):
        assert table is not None  # resolve_profiler enforced this for Iceberg
        return profile_iceberg(
            connection,
            table=table,
            namespace=namespace,
            columns=columns,
            top_n=top_n,
            secret_store=secret_store,
        )
    if isinstance(profiler, _SqlProfiler):
        assert table is not None  # resolve_profiler enforced this for SQL types
        return profile_table(
            connection,
            table=table,
            schema=schema,
            catalog=catalog,
            columns=columns,
            top_n=top_n,
            secret_store=secret_store,
        )
    assert path is not None  # resolve_profiler enforced this for flat-file types
    return profile_file(
        connection,
        path=path,
        file_format=file_format,
        columns=columns,
        top_n=top_n,
        secret_store=secret_store,
    )


# ───────────────────────── column listing (introspection) ──────────
#
# A read-only "what columns does this target have?" lookup, so the check editor
# can offer a column *dropdown* instead of free-text (#474). Reuses the same
# connection plumbing, target dispatch, and identifier validation as the
# profiler — it's the same target, just names instead of stats.


def list_table_columns(
    connection: Connection,
    *,
    table: str,
    schema: str | None,
    catalog: str | None = None,
    secret_store: SecretStore,
) -> list[str]:
    """Column names of a SQL `table` on `connection` (Snowflake / Unity Catalog).

    Raises `ProfileIdentifierInvalidError` (422) for a bad catalog/schema/table
    (validated before any query runs) and `ProfileFailedError` (502) if the
    lookup can't execute — the adapter exception is never echoed.
    """
    effective_schema = resolve_effective_schema(connection, schema)
    # Validate every identifier up front (422) before any query is built/run.
    if catalog is not None:
        validate_identifier(catalog)
    validate_identifier(table)
    validate_identifier(effective_schema)

    try:
        with _open_connection(connection, secret_store) as conn:
            result = conn.execute(build_columns_query(effective_schema, table, catalog))
            return list(result.keys())
    except Exception as exc:
        log.warning(
            "column_list_failed", connection_type=connection.type, error_type=type(exc).__name__
        )
        raise ProfileFailedError(
            "columns could not be listed from the datasource", detail={"table": table}
        ) from exc


def list_file_columns(
    connection: Connection,
    *,
    path: str,
    file_format: str | None,
    secret_store: SecretStore,
) -> list[str]:
    """Column (header) names of a flat file on `connection` (ADLS Gen2 / S3).

    Reads only the header (CSV `nrows=0`) or the Parquet footer schema — no data
    scan. Raises `ProfileTargetInvalidError` (422) for an unknown format and
    `ProfileFailedError` (502) if the file can't be read (exception not echoed).
    """
    import pandas as pd

    # secret_ref presence is guaranteed by the dispatcher (`resolve_profiler`),
    # as in `profile_file`; a direct call without it surfaces as a read failure.
    fmt = infer_file_format(path, file_format)
    try:
        secret = secret_store.get(connection.secret_ref or "")
        raw = io.BytesIO(
            download_bytes(
                conn_type=connection.type, config=connection.config, path=path, secret=secret
            )
        )
        if fmt == "csv":
            return [str(c) for c in pd.read_csv(raw, nrows=0).columns]
        import pyarrow.parquet as pq

        return [str(name) for name in pq.ParquetFile(raw).schema.names]
    except Exception as exc:
        log.warning(
            "column_list_failed", connection_type=connection.type, error_type=type(exc).__name__
        )
        raise ProfileFailedError(
            "columns could not be read from the file", detail={"path": path}
        ) from exc


def list_columns(
    connection: Connection,
    *,
    table: str | None = None,
    schema: str | None = None,
    catalog: str | None = None,
    namespace: str | None = None,
    path: str | None = None,
    file_format: str | None = None,
    secret_store: SecretStore,
) -> list[str]:
    """List a target's column names, dispatching on the connection type.

    Same target rules as `profile_connection` (a SQL/Iceberg type needs `table`;
    Unity Catalog also needs `catalog`; a flat-file type needs `path`). Raises
    `ProfileUnsupportedError` (422) for a type with no profiler and
    `ProfileTargetInvalidError` (422) for a missing target/credential.
    """
    profiler = resolve_profiler(connection, table=table, catalog=catalog, path=path)
    if isinstance(profiler, _IcebergProfiler):
        assert table is not None  # resolve_profiler enforced this for Iceberg
        return list_iceberg_columns(
            connection, table=table, namespace=namespace, secret_store=secret_store
        )
    if isinstance(profiler, _SqlProfiler):
        assert table is not None  # resolve_profiler enforced this for SQL types
        return list_table_columns(
            connection, table=table, schema=schema, catalog=catalog, secret_store=secret_store
        )
    assert path is not None  # resolve_profiler enforced this for flat-file types
    return list_file_columns(
        connection, path=path, file_format=file_format, secret_store=secret_store
    )


def suggest_policy_for_target(
    connection: Connection,
    *,
    table: str | None = None,
    schema: str | None = None,
    catalog: str | None = None,
    namespace: str | None = None,
    path: str | None = None,
    file_format: str | None = None,
    top_n: int = 20,
    secret_store: SecretStore,
) -> dict[str, Any]:
    """List → profile → classify a target's columns into a redaction-policy suggestion.

    The shared engine behind both the "Auto-detect" endpoint and the auto-classify
    task (#634): introspect the target's column names, profile them for sample
    values, then `derive_column_policy` into ``{identifier_column?, pii_columns}``.
    Raises the profiler's ``ProfileUnsupportedError`` / ``ProfileTargetInvalidError``
    (422s) for an unprofilable type or a missing/invalid target — the task treats
    those as a fail-soft no-op; the endpoint surfaces them.
    """
    columns = list_columns(
        connection,
        table=table,
        schema=schema,
        catalog=catalog,
        namespace=namespace,
        path=path,
        file_format=file_format,
        secret_store=secret_store,
    )
    result = profile_connection(
        connection,
        columns=columns,
        top_n=top_n,
        table=table,
        schema=schema,
        catalog=catalog,
        namespace=namespace,
        path=path,
        file_format=file_format,
        secret_store=secret_store,
    )
    return derive_column_policy(result.columns)
