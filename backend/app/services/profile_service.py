"""Column profiler — per-column statistics for the check editor."""

from __future__ import annotations

import io
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import column, distinct, func, literal_column, select, union_all
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql import Select

if TYPE_CHECKING:
    from sqlalchemy.engine.interfaces import Dialect

from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.jsonsafe import sanitize_json
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.datasources.flatfile import (
    STREAM_CHUNK,
    RangeReader,
    download_bytes,
    format_from_path,
    read_csv_bytes,
    read_csv_head,
)
from backend.app.datasources.iceberg import (
    IcebergConfig,
    iceberg_credentials,
    load_iceberg_table,
    read_iceberg_dataframe,
)
from backend.app.datasources.iceberg import list_iceberg_columns as iceberg_column_names
from backend.app.datasources.snowflake import (
    SnowflakeConfig,
    build_connect_args,
    build_connection_string,
)
from backend.app.datasources.sql import core_table, folding_identifier, is_sql_identifier
from backend.app.datasources.unity_catalog import UnityCatalogConfig, build_databricks_url
from backend.app.db.models import Connection
from backend.app.services import credential_health
from backend.app.services.column_classification import ColumnClass, classify_column

log = get_logger(__name__)

# Formats the profiler can actually parse.
_SUPPORTED_FORMATS = {"csv", "parquet"}
# Flat-file profiling reads at most this many rows — stats are over the sample.
_SAMPLE_ROWS = 100_000
#: Public alias — the MCP layer RETURNS this value (`profile_column`'s `sample_row_limit`).
SAMPLE_ROWS = _SAMPLE_ROWS
# How many columns share one batched top-values round-trip (#327).
_TOP_VALUES_BATCH = 25


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
    `table` / `schema`, flat-file datasources set `path` / `file_format`.
    """

    row_count: int
    columns: list[ColumnProfile]
    table: str | None = None
    schema: str | None = None
    catalog: str | None = None
    path: str | None = None
    file_format: str | None = None


# ───────────────────────── shared stat contract ────────────────────


def null_fraction(null_count: int, row_count: int) -> float:
    """Fraction of rows that are null, guarding the empty-target divide-by-zero."""
    return (null_count / row_count) if row_count else 0.0


# ───────────────────────── pure query builders ─────────────────────


def validate_identifier(name: str | None) -> str:
    """Validate `name` against the plain-identifier allowlist and return it."""
    if name is None or not is_sql_identifier(name):
        raise ProfileIdentifierInvalidError(
            "not a valid table/schema/column identifier", detail={"identifier": name}
        )
    return name


def _table(
    schema: str, table_name: str, catalog: str | None = None, dialect: Dialect | None = None
) -> Any:
    """A Core table clause, optionally with a 3-level namespace (Unity Catalog)."""
    validate_identifier(schema)
    validate_identifier(table_name)
    if catalog is not None:
        validate_identifier(catalog)
    return core_table(table=table_name, schema=schema, catalog=catalog, dialect=dialect)


def build_aggregate_query(
    schema: str,
    table_name: str,
    columns: list[str],
    catalog: str | None = None,
    dialect: Dialect | None = None,
) -> Select[Any]:
    """One round-trip: row count + null/distinct/min/max per column."""
    projection: list[Any] = [func.count().label("row_count")]
    for i, col in enumerate(columns):
        c: Any = column(folding_identifier(validate_identifier(col)))
        projection.append((func.count() - func.count(c)).label(f"nulls_{i}"))
        projection.append(func.count(distinct(c)).label(f"distinct_{i}"))
        projection.append(func.min(c).label(f"min_{i}"))
        projection.append(func.max(c).label(f"max_{i}"))
    return select(*projection).select_from(_table(schema, table_name, catalog, dialect))


# Output labels for the top-values query.
_VALUE_LABEL = "dq_value"
_FREQ_LABEL = "dq_freq"
_RANK_LABEL = "dq_rn"


def _grouped_column(col: str) -> tuple[Any, list[Any]]:
    """The validated column expression + the ordering every top-values query uses."""
    c: Any = column(folding_identifier(validate_identifier(col)))
    return c, [func.count().desc(), c]


def build_top_values_query(
    schema: str,
    table_name: str,
    col: str,
    top_n: int,
    catalog: str | None = None,
    dialect: Dialect | None = None,
) -> Select[Any]:
    """Most frequent non-null values for one column (highest count first)."""
    c, ordering = _grouped_column(col)
    return (
        select(c.label(_VALUE_LABEL), func.count().label(_FREQ_LABEL))
        .select_from(_table(schema, table_name, catalog, dialect))
        .where(c.is_not(None))
        .group_by(c)
        .order_by(*ordering)
        .limit(int(top_n))
    )


def build_batched_top_values_query(
    schema: str,
    table_name: str,
    columns: list[str],
    top_n: int,
    catalog: str | None = None,
    dialect: Dialect | None = None,
) -> Select[Any]:
    """Every column's top values in **one** round-trip (#327)."""
    if not columns:
        raise ValueError("build_batched_top_values_query needs at least one column")
    limit = int(top_n)
    if limit < 1:
        raise ValueError("build_batched_top_values_query needs a positive top_n")
    # Ranks are loop counters, not caller input — `literal_column` keeps them out of the bind-
    # parameter set and makes the driver a plain integer union, which has nothing to unify.
    ranks = union_all(
        *[select(literal_column(str(rank)).label("rn")) for rank in range(1, limit + 1)]
    ).subquery("dq_ranks")

    joined: Any = ranks
    projection: list[Any] = [ranks.c.rn.label("rn")]
    for i, col in enumerate(columns):
        _, ordering = _grouped_column(col)
        top = (
            build_top_values_query(schema, table_name, col, limit, catalog, dialect)
            .add_columns(func.row_number().over(order_by=ordering).label(_RANK_LABEL))
            .subquery(f"dq_top_{i}")
        )
        joined = joined.join(top, top.c[_RANK_LABEL] == ranks.c.rn, isouter=True)
        projection.extend([top.c[_VALUE_LABEL].label(f"v_{i}"), top.c[_FREQ_LABEL].label(f"f_{i}")])
    return select(*projection).select_from(joined).order_by(ranks.c.rn)


def collect_batched_top_values(
    rows: list[Mapping[str, Any]], columns: list[str]
) -> dict[str, list[Mapping[str, Any]]]:
    """Un-pivot `build_batched_top_values_query`'s rows into the per-column shape
    `assemble_profile` consumes — ``{column: [{"value": …, "freq": int}, …]}``.
    """
    by_index: dict[int, list[Mapping[str, Any]]] = {}
    for row in sorted(rows, key=lambda item: int(item["rn"])):
        for index in range(len(columns)):
            freq = row[f"f_{index}"]
            if freq is None:
                continue
            by_index.setdefault(index, []).append({"value": row[f"v_{index}"], "freq": freq})
    # Keyed by index then remapped, so a repeated column name collapses to one
    # entry exactly as the per-column path's dict comprehension did.
    return {columns[index]: entries for index, entries in by_index.items()}


def build_columns_query(
    schema: str, table_name: str, catalog: str | None = None, dialect: Dialect | None = None
) -> Select[Any]:
    """List a target's column names: `SELECT * FROM <target> LIMIT 0`."""
    return (
        select(literal_column("*"))
        .select_from(_table(schema, table_name, catalog, dialect))
        .limit(0)
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
    """Iceberg profiling strategy: native ``pyiceberg`` read into pandas (ADR 0030)."""


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
    """
    profiler = _PROFILERS.get(connection.type)
    if profiler is None:
        raise ProfileUnsupportedError(
            f"column introspection is not supported for {connection.type!r} connections in v1",
            detail={"type": connection.type, "supported": sorted(_PROFILERS)},
        )
    # Iceberg is credential-optional (like `build_iceberg_runner` / the ADLS/S3 adapters) — a local
    # warehouse or vended-credentials REST catalog has no secret.
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
    when neither is set. Shared by `profile_table` and `list_table_columns`.
    """
    effective_schema = schema if schema is not None else connection.config.get("schema")
    if not isinstance(effective_schema, str):
        raise ProfileIdentifierInvalidError(
            "no schema given and the connection has none", detail={"schema": effective_schema}
        )
    return effective_schema


def _top_values_per_column(
    conn: Any,
    *,
    schema: str,
    table: str,
    columns: list[str],
    top_n: int,
    catalog: str | None,
    dialect: Dialect | None,
) -> dict[str, list[Mapping[str, Any]]]:
    """The original path: one grouped-and-limited query per column, N round-trips."""
    return {
        col: [
            {"value": row[_VALUE_LABEL], "freq": row[_FREQ_LABEL]}
            for row in conn.execute(
                build_top_values_query(schema, table, col, top_n, catalog, dialect)
            ).mappings()
        ]
        for col in columns
    }


def _recover_transaction(conn: Any) -> None:
    """Roll back so the fallback can run on a usable connection (#327 review, P1)."""
    try:
        conn.rollback()
    except Exception as exc:
        log.warning("profile_top_values_rollback_failed", error_type=type(exc).__name__)


def _fetch_top_values(
    conn: Any,
    *,
    schema: str,
    table: str,
    columns: list[str],
    top_n: int,
    catalog: str | None,
    dialect: Dialect | None,
) -> dict[str, list[Mapping[str, Any]]]:
    """Top values for every column, batched, with the per-column path as fallback."""
    if not columns:
        return {}
    if int(top_n) < 1:
        # No top values were asked for.
        return {col: [] for col in columns}

    collected: dict[str, list[Mapping[str, Any]]] = {}
    for start in range(0, len(columns), _TOP_VALUES_BATCH):
        batch = columns[start : start + _TOP_VALUES_BATCH]
        # Built outside the try: a builder failure is a rejected identifier (422),
        # not a dialect disagreement, and must not be retried per column.
        statement = build_batched_top_values_query(schema, table, batch, top_n, catalog, dialect)
        try:
            rows = list(conn.execute(statement).mappings())
        except SQLAlchemyError as exc:
            log.warning(
                "profile_top_values_batch_fallback",
                error_type=type(exc).__name__,
                batch_columns=len(batch),
            )
            _recover_transaction(conn)
            collected.update(
                _top_values_per_column(
                    conn,
                    schema=schema,
                    table=table,
                    columns=batch,
                    top_n=top_n,
                    catalog=catalog,
                    dialect=dialect,
                )
            )
            continue
        collected.update(collect_batched_top_values(rows, batch))
    return collected


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
            # A catalog-qualified (3-part, Unity Catalog) target needs the live connection's dialect
            # to quote the catalog/schema (#936).
            dialect = conn.dialect if catalog is not None else None
            aggregate = (
                conn.execute(
                    build_aggregate_query(effective_schema, table, columns, catalog, dialect)
                )
                .mappings()
                .one()
            )
            top_values = _fetch_top_values(
                conn,
                schema=effective_schema,
                table=table,
                columns=columns,
                top_n=top_n,
                catalog=catalog,
                dialect=dialect,
            )
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
    """Resolve the file format from an explicit value or the path extension."""
    fmt = explicit or format_from_path(path)
    if fmt not in _SUPPORTED_FORMATS:
        raise ProfileTargetInvalidError(
            "cannot determine a supported file format; pass file_format",
            detail={"path": path, "supported": sorted(_SUPPORTED_FORMATS)},
        )
    return fmt


def _to_native(value: Any) -> Any:
    """Coerce a cell to the JSON-friendly value the SQL/warehouse path would produce —
    one root (`sanitize_json`) for every driver type (#1721/#1803/#1804); anything it
    leaves opaque (UUID, …) becomes a display string, never a 500 at the response boundary.
    """
    native = sanitize_json(value)
    if native is None or isinstance(native, bool | int | float | str):
        return native
    return str(native)


def _profile_columns(df: Any, *, columns: list[str], top_n: int) -> tuple[int, list[ColumnProfile]]:
    """Row count + per-column stats for `columns` of an in-memory dataframe."""
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
    """Compute per-column stats from an in-memory dataframe (pure, no I/O)."""
    row_count, profiles = _profile_columns(df, columns=columns, top_n=top_n)
    return ProfileResult(path=path, file_format=file_format, row_count=row_count, columns=profiles)


def _profile_series(column: str, series: Any, *, row_count: int, top_n: int) -> ColumnProfile:
    """Per-column stats, degrading a messy column to nulls instead of 500-ing."""
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
    """Read `path` from the flat-file datasource into a sampled dataframe."""
    if not connection.secret_ref:
        raise ValueError("connection requires secret_ref for the credential")
    secret = secret_store.get(connection.secret_ref)

    if file_format == "parquet":
        return _read_parquet_sample(
            conn_type=connection.type,
            config=connection.config,
            path=path,
            secret=secret,
            columns=columns,
        )

    wanted = set(columns)
    raw = io.BytesIO(
        download_bytes(
            conn_type=connection.type, config=connection.config, path=path, secret=secret
        )
    )
    return read_csv_bytes(raw, nrows=_SAMPLE_ROWS, usecols=lambda name: name in wanted)


def _read_parquet_sample(
    *, conn_type: str, config: dict[str, Any], path: str, secret: str, columns: list[str]
) -> Any:
    """Sample a Parquet file's projected columns without downloading it (#1001)."""
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    reader = RangeReader(
        conn_type=conn_type, config=config, path=path, secret=secret, chunk=STREAM_CHUNK
    )
    parquet_file = pq.ParquetFile(reader)
    available = set(parquet_file.schema.names)
    present = [c for c in columns if c in available]
    if not present:
        # No requested column exists in the file — `profile_dataframe` reports the missing names as
        # a clean 422 off `columns`, not off this frame.
        return pd.DataFrame()

    schema = parquet_file.schema_arrow
    batches = []
    rows = 0
    for batch in parquet_file.iter_batches(batch_size=_SAMPLE_ROWS, columns=present):
        batches.append(batch)
        rows += batch.num_rows
        if rows >= _SAMPLE_ROWS:
            break

    if batches:
        table = pa.Table.from_batches(batches)
    else:
        # A real, correctly-typed empty table (e.g. a header-only file) rather than an untyped
        # `pd.DataFrame(columns=present)`.
        table = pa.table({c: pa.array([], type=schema.field(c).type) for c in present})
    # Parquet is already Arrow on disk; dtype_backend="pyarrow" keeps the buffers zero-copy instead
    # of materialising a numpy copy.
    return table.to_pandas(types_mapper=pd.ArrowDtype).head(_SAMPLE_ROWS)


def profile_file(
    connection: Connection,
    *,
    path: str,
    file_format: str | None,
    columns: list[str],
    top_n: int,
    secret_store: SecretStore,
) -> ProfileResult:
    """Profile `columns` of a flat file on `connection` (ADLS Gen2 / S3)."""
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
    Iceberg branch, so the profiler and the run path resolve the same table.
    """
    folded = namespace if isinstance(namespace, str) and namespace.strip() else None
    return f"{folded}.{table}" if folded else table


def _read_iceberg_dataframe(
    connection: Connection, *, identifier: str, columns: list[str], secret_store: SecretStore
) -> Any:
    """Resolve an Iceberg connection's config + optional secret (exactly as `build_iceberg_runner`
    does — the credential is optional), load the table ONCE, validate the requested `columns`
    against its schema *before any scan* (raising the same `ProfileColumnNotFoundError` the
    post-scan defence-in-depth check in `_profile_columns` raises — same message/detail shape —
    so an all-or-partially-invalid column list 422s without ever reading data), then materialise
    the already-loaded table as a projected, sampled DataFrame.
    """
    config = IcebergConfig.model_validate(connection.config)
    secret, catalog_secret = iceberg_credentials(config, connection.secret_ref, secret_store)
    table = load_iceberg_table(config, secret, identifier, catalog_secret)
    available = [field.name for field in table.schema().fields]
    missing = [c for c in columns if c not in available]
    if missing:
        raise ProfileColumnNotFoundError(
            "requested column(s) not in the target",
            detail={"missing": missing, "available": available[:50]},
        )
    return read_iceberg_dataframe(
        config,
        secret,
        identifier,
        columns=columns,
        limit=_SAMPLE_ROWS,
        table=table,
        catalog_secret=catalog_secret,
    )


def _list_iceberg_columns(
    connection: Connection, *, identifier: str, secret_store: SecretStore
) -> list[str]:
    """Resolve config + optional secret and list the target's schema field names
    (metadata only, no data scan) — the Iceberg column-listing I/O seam.
    """
    config = IcebergConfig.model_validate(connection.config)
    secret, catalog_secret = iceberg_credentials(config, connection.secret_ref, secret_store)
    return iceberg_column_names(config, secret, identifier, catalog_secret)


def profile_iceberg(
    connection: Connection,
    *,
    table: str,
    namespace: str | None,
    columns: list[str],
    top_n: int,
    secret_store: SecretStore,
) -> ProfileResult:
    """Profile `columns` of a natively-read Iceberg `table` on `connection` (#721)."""
    identifier = _iceberg_identifier(table, namespace)
    try:
        df = _read_iceberg_dataframe(
            connection, identifier=identifier, columns=columns, secret_store=secret_store
        )
    except ProfileColumnNotFoundError:
        raise  # the pre-scan column-validation 422 — not an adapter failure
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
    """Column (field) names of an Iceberg `table` on `connection` — no data scan."""
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
    """Auto-derive a failing-sample redaction policy (#415) from a column profile."""
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
    session: Session,
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
    """Dispatch to the SQL, flat-file, or Iceberg profiler based on the type."""
    # Credential-health seam (#1697) — every profiler branch reads live data with this
    # connection's stored credential.
    with credential_health.credential_use(session, connection):
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
    """Column names of a SQL `table` on `connection` (Snowflake / Unity Catalog)."""
    effective_schema = resolve_effective_schema(connection, schema)
    # Validate every identifier up front (422) before any query is built/run.
    if catalog is not None:
        validate_identifier(catalog)
    validate_identifier(table)
    validate_identifier(effective_schema)

    try:
        with _open_connection(connection, secret_store) as conn:
            dialect = conn.dialect if catalog is not None else None
            result = conn.execute(build_columns_query(effective_schema, table, catalog, dialect))
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
    """Column (header) names of a flat file on `connection` (ADLS Gen2 / S3)."""
    # secret_ref presence is guaranteed by the dispatcher (`resolve_profiler`),
    # as in `profile_file`; a direct call without it surfaces as a read failure.
    fmt = infer_file_format(path, file_format)
    try:
        secret = secret_store.get(connection.secret_ref or "")
        reader_args: dict[str, Any] = {
            "conn_type": connection.type,
            "config": connection.config,
            "path": path,
            "secret": secret,
        }
        if fmt == "csv":
            return [str(c) for c in read_csv_head(**reader_args, rows=0).columns]
        import pyarrow.parquet as pq

        return [str(name) for name in pq.ParquetFile(RangeReader(**reader_args)).schema.names]
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
    session: Session,
    table: str | None = None,
    schema: str | None = None,
    catalog: str | None = None,
    namespace: str | None = None,
    path: str | None = None,
    file_format: str | None = None,
    secret_store: SecretStore,
) -> list[str]:
    """List a target's column names, dispatching on the connection type."""
    # Credential-health seam (#1697) — introspection opens the datasource with this
    # connection's stored credential, on every type (SQL, flat-file, Iceberg).
    with credential_health.credential_use(session, connection):
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
    session: Session,
    table: str | None = None,
    schema: str | None = None,
    catalog: str | None = None,
    namespace: str | None = None,
    path: str | None = None,
    file_format: str | None = None,
    top_n: int = 20,
    secret_store: SecretStore,
) -> dict[str, Any]:
    """List → profile → classify a target's columns into a redaction-policy suggestion."""
    columns = list_columns(
        connection,
        session=session,
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
        session=session,
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
