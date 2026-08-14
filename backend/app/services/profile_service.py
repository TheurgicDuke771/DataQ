"""Column profiler — per-column statistics for the check editor.

Given a target (a SQL table or a flat file) and a set of columns on a suite's
connection, compute the stats an author needs before writing expectations: row
count, null count / fraction, distinct count, min / max, and the most frequent
values. Persists nothing — a read-only authoring aid (the check-editor "profile
on table/file select" panel).

`profile_connection` dispatches on the connection type:

* **SQL datasources** (Snowflake + Unity Catalog) — aggregate the stats
  in-warehouse in one round-trip, plus one *batched* top-values round-trip per
  `_TOP_VALUES_BATCH` columns (#327 — it used to be one query per column, so a
  50-column profile cost 51 sequential round-trips and now costs 3), via the
  datasource's SQLAlchemy dialect. Unity Catalog adds a `catalog` so the table is qualified
  `catalog.schema.table` (3-level namespace); Snowflake is `schema.table`.
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
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import column, distinct, func, literal_column, select, union_all
from sqlalchemy.sql import Select

if TYPE_CHECKING:
    from sqlalchemy.engine.interfaces import Dialect

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
# How many columns share one batched top-values round-trip (#327). The batched
# query joins one derived table per column (see `build_batched_top_values_query`),
# and past a couple of dozen relations a planner stops enumerating join orders
# exhaustively — Postgres switches to its genetic optimiser at 12 — so the batch
# is bounded here rather than left to grow with the caller's column list.
#
# 25 is measured, not guessed. On a 50-column / 50k-row local Postgres the whole
# top-values pass took ~270 ms at every chunk size from 8 to 25 and jumped to
# ~820 ms as a single 50-way join, while round-trips fall monotonically with the
# chunk size. 25 sits at the last point before that cliff, which puts the profile
# endpoint's own 50-column cap at exactly TWO batched round-trips (previously 50)
# and still bounds `suggest_policy_for_target` — the one caller that profiles
# every column a target has — to a round-trip per 25 columns instead of per 1.
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
    identifier per the shared `datasources.sql` allowlist (#428 — one source of
    truth with the monitor engine's validator). The SQLAlchemy Core builders
    quote safely on their own; this is defence-in-depth and turns an odd name
    into a clean 422 instead of a quoted column that simply doesn't exist.
    """
    if name is None or not is_sql_identifier(name):
        raise ProfileIdentifierInvalidError(
            "not a valid table/schema/column identifier", detail={"identifier": name}
        )
    return name


def _table(
    schema: str, table_name: str, catalog: str | None = None, dialect: Dialect | None = None
) -> Any:
    """A Core table clause, optionally with a 3-level namespace (Unity Catalog).

    Construction is the shared `datasources.sql.core_table` (#476 — one builder
    with the monitor engine, so the two can't drift on quoting). Validating here
    first is not redundant: it turns an odd name into a clean 422 with the
    profiler's error shape, where `core_table`'s own guard is the last-resort
    injection check and raises a bare `ValueError`.

    ``dialect`` is required whenever ``catalog`` is given (#936) — it quotes the
    3-part namespace's catalog/schema via the dialect's own identifier preparer;
    see `datasources.sql.core_table`.
    """
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
    """One round-trip: row count + null/distinct/min/max per column.

    Built with the Core expression language (no string SQL); identifiers are
    validated then handed to `column()`/`table()`, which the dialect quotes.
    """
    projection: list[Any] = [func.count().label("row_count")]
    for i, col in enumerate(columns):
        c: Any = column(folding_identifier(validate_identifier(col)))
        projection.append((func.count() - func.count(c)).label(f"nulls_{i}"))
        projection.append(func.count(distinct(c)).label(f"distinct_{i}"))
        projection.append(func.min(c).label(f"min_{i}"))
        projection.append(func.max(c).label(f"max_{i}"))
    return select(*projection).select_from(_table(schema, table_name, catalog, dialect))


def build_top_values_query(
    schema: str,
    table_name: str,
    col: str,
    top_n: int,
    catalog: str | None = None,
    dialect: Dialect | None = None,
) -> Select[Any]:
    """Most frequent non-null values for one column (highest count first)."""
    c: Any = column(folding_identifier(validate_identifier(col)))
    freq = func.count().label("freq")
    return (
        select(c.label("value"), freq)
        .select_from(_table(schema, table_name, catalog, dialect))
        .where(c.is_not(None))
        .group_by(c)
        .order_by(func.count().desc(), c)
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
    """Every column's top values in **one** round-trip (#327).

    Shape: a rank driver (``1..top_n``) LEFT-joined to one derived table per
    column, each of which is *exactly* the query `build_top_values_query` builds
    — same ``WHERE col IS NOT NULL``, same ``GROUP BY col``, same
    ``ORDER BY count DESC, col``, same ``LIMIT`` — plus a ``ROW_NUMBER()``
    computed with that same ordering, which is what the join keys on::

        SELECT r.rn, t0.value AS v_0, t0.freq AS f_0, t1.value AS v_1, …
        FROM (SELECT 1 AS rn UNION ALL SELECT 2 AS rn …) AS r
        LEFT JOIN (SELECT ROW_NUMBER() OVER (…) AS rn, c0 AS value, count(*) AS freq
                   FROM tbl WHERE c0 IS NOT NULL GROUP BY c0
                   ORDER BY count(*) DESC, c0 LIMIT n) AS t0 ON t0.rn = r.rn
        LEFT JOIN (…same for c1…) AS t1 ON t1.rn = r.rn
        ORDER BY r.rn

    **Why a join and not the obvious ``UNION ALL`` of per-column selects.** That
    was tried first and it is the trap #327 itself flagged. A union has to unify
    the branches' column types, so every column's values land in one ``value``
    projection: Postgres refuses ``integer``/``text`` outright, Snowflake
    silently coerces the lot to ``VARCHAR``, and the profile's numbers come back
    as strings on one dialect and not the other. Giving each column its own slot
    and padding the other branches with ``NULL`` does not rescue it either — an
    untyped ``NULL`` is resolved *pairwise, left to right*, so in a left-deep
    union tree two ``NULL`` padding slots resolve to ``text`` before the branch
    that owns the slot is ever reached, and Postgres then rejects the whole
    statement (verified: ``UNION types text and timestamp … cannot be matched``).
    A join never unifies anything: each column's value stays in its own output
    column with its own type, so a NUMERIC arrives as a ``Decimal`` and a
    TIMESTAMP as a datetime, exactly as the per-column path delivered them.

    It is also the *small* shape — ``top_n`` rows of ``2N+1`` columns, rather
    than the union's N-by-N projection over ``N * top_n`` rows. What it costs
    instead is join planning, which is why `_TOP_VALUES_BATCH` caps N.

    ``ROW_NUMBER()`` carries the per-column ordering across the join: a joined
    result has no inherent order, and re-sorting *values* in Python would not
    reproduce the warehouse's collation for a count tie. The outer ``ORDER BY``
    plus the rank key make the emitted order the per-column query's order,
    ties included.

    Built with the Core expression language throughout, so the dialect quotes
    every identifier and renders the joins/derived tables itself — no string SQL.
    """
    if not columns:
        raise ValueError("build_batched_top_values_query needs at least one column")
    limit = int(top_n)
    if limit < 1:
        raise ValueError("build_batched_top_values_query needs a positive top_n")
    target = _table(schema, table_name, catalog, dialect)
    # Ranks are loop counters, not caller input — `literal_column` keeps them out
    # of the bind-parameter set and makes the driver a plain integer union, which
    # has nothing to unify.
    ranks = union_all(
        *[select(literal_column(str(rank)).label("rn")) for rank in range(1, limit + 1)]
    ).subquery("dq_ranks")

    joined: Any = ranks
    projection: list[Any] = [ranks.c.rn.label("rn")]
    for i, col in enumerate(columns):
        c: Any = column(folding_identifier(validate_identifier(col)))
        ordering = (func.count().desc(), c)
        top = (
            select(
                func.row_number().over(order_by=list(ordering)).label("rn"),
                c.label("value"),
                func.count().label("freq"),
            )
            .select_from(target)
            .where(c.is_not(None))
            .group_by(c)
            .order_by(*ordering)
            .limit(limit)
            .subquery(f"dq_top_{i}")
        )
        joined = joined.join(top, top.c.rn == ranks.c.rn, isouter=True)
        projection.extend([top.c.value.label(f"v_{i}"), top.c.freq.label(f"f_{i}")])
    return select(*projection).select_from(joined).order_by(ranks.c.rn)


def collect_batched_top_values(
    rows: list[Mapping[str, Any]], columns: list[str]
) -> dict[str, list[Mapping[str, Any]]]:
    """Un-pivot `build_batched_top_values_query`'s rows into the per-column shape
    `assemble_profile` consumes — ``{column: [{"value": …, "freq": int}, …]}``.

    One row is one *rank*: column *i*'s rank-``rn`` value sits in ``v_i`` with its
    count in ``f_i``. A column with fewer distinct values than ``top_n`` — or an
    all-null column, which contributes nothing — simply has no match at that rank,
    so the LEFT JOIN leaves both slots NULL. ``f_i`` is the presence marker rather
    than ``v_i`` because it is the one that cannot be NULL for a real row (the
    per-column query filters ``IS NOT NULL``, and a COUNT is never null).

    Rows are re-sorted by ``rn`` rather than trusted in arrival order: the outer
    ``ORDER BY`` already asks for it, and paying one sort makes the reader
    independent of whether a given driver honours it.

    Columns that contributed no rows at all are simply absent from the result —
    `assemble_profile` renders a missing key as ``[]``, which is what the
    per-column path's empty result set produced.
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
    """List a target's column names: `SELECT * FROM <target> LIMIT 0`.

    Returns no rows, but the cursor still exposes the column names via
    `result.keys()` — so it's a cheap, dialect-agnostic way to introspect columns
    that reuses the same catalog-aware, allowlist-validated `_table` namespace as
    the profiler (rather than the SQLAlchemy inspector, which is fiddly for Unity
    Catalog's 3-level `catalog.schema.table`). `literal_column("*")` is a SQL
    constant, not caller input — the only caller-supplied parts go through
    `_table`'s identifier validation.
    """
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
    """The original path: one grouped-and-limited query per column, N round-trips.

    Kept as an explicit, reachable fallback (#327's own instruction), not as dead
    code — see `_fetch_top_values`.
    """
    return {
        col: list(
            conn.execute(
                build_top_values_query(schema, table, col, top_n, catalog, dialect)
            ).mappings()
        )
        for col in columns
    }


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
    """Top values for every column, batched, with the per-column path as fallback.

    Tries `build_batched_top_values_query` in chunks of `_TOP_VALUES_BATCH` — the
    #327 win, turning 1 + N round-trips into 1 + ceil(N / _TOP_VALUES_BATCH). If *any* chunk
    raises, the whole set is re-collected column-by-column via
    `_top_values_per_column`, so a dialect that mishandles the union form (a
    parenthesised ``LIMIT`` inside a set operation, a window function over an
    aggregate, an untyped ``NULL`` slot) degrades to the pre-#327 behaviour
    instead of failing the profile. Cross-dialect SQL is only ever proven by a
    live run, so the fallback is the standing answer to "what if a warehouse
    disagrees", not a hypothetical.

    The fallback is deliberately *not* a silent one: it logs
    ``profile_top_values_batch_fallback`` with the exception type, so a warehouse
    that always falls back is visible in telemetry rather than merely slow. A
    `DataQError` (a bad identifier the builder refused) is re-raised untouched —
    the per-column builder validates identically, so retrying it would only turn
    one clean 422 into two.
    """
    if not columns:
        return {}
    if int(top_n) < 1:
        # The batched form's rank driver needs at least one rank; the per-column
        # query's own `LIMIT 0` already answers this, so use it rather than
        # inventing a second definition of "no top values".
        return _top_values_per_column(
            conn,
            schema=schema,
            table=table,
            columns=columns,
            top_n=top_n,
            catalog=catalog,
            dialect=dialect,
        )
    try:
        collected: dict[str, list[Mapping[str, Any]]] = {}
        for start in range(0, len(columns), _TOP_VALUES_BATCH):
            batch = columns[start : start + _TOP_VALUES_BATCH]
            rows = list(
                conn.execute(
                    build_batched_top_values_query(schema, table, batch, top_n, catalog, dialect)
                ).mappings()
            )
            collected.update(collect_batched_top_values(rows, batch))
        return collected
    except DataQError:
        raise
    except Exception as exc:
        log.warning("profile_top_values_batch_fallback", error_type=type(exc).__name__)
    return _top_values_per_column(
        conn,
        schema=schema,
        table=table,
        columns=columns,
        top_n=top_n,
        catalog=catalog,
        dialect=dialect,
    )


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
            # A catalog-qualified (3-part, Unity Catalog) target needs the live
            # connection's dialect to quote the catalog/schema (#936); a 2-part
            # Snowflake target never reaches the code that would use it.
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
    """Read `path` from the flat-file datasource into a sampled dataframe.

    The live I/O seam (download/range-read + parse) — monkeypatched in tests.
    Applies the two "load less data" levers from the pandas scaling guide:

    * **column projection** — only the requested `columns` are parsed (CSV
      `usecols`, Parquet `columns=`), so profiling 3 of 200 columns doesn't read
      all 200. Unknown names are simply not selected; `profile_dataframe` then
      reports genuinely-missing ones as a clean 422.
    * **row sampling** — at most `_SAMPLE_ROWS` rows.

    **Parquet** (#1001) goes through the `RangeReader` seam #882 gave column
    listing, rather than downloading the whole object — see
    `_read_parquet_sample` below.

    **CSV stays on whole-object download**, deliberately. A CSV has no footer
    and no fixed row width, so a bounded head range can only bound *bytes*, not
    rows — on a wide file that silently caps the sample below `_SAMPLE_ROWS`
    with no signal that it happened, changing reported distinct counts / top
    values / min-max (the #839 lesson: a quietly-shrunk statistic is worse than
    the egress it saves). Parquet has no such trap because its row groups are
    self-describing, which is exactly why it's the format fixed here.
    """
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
    """Sample a Parquet file's projected columns without downloading it (#1001).

    Same `RangeReader` seam #882 gave column listing: `pq.ParquetFile` lands on
    the footer with a couple of small range GETs regardless of object size, then
    `iter_batches(columns=present)` streams row groups off the object — not the
    file's other columns, and not its later rows once the sample is met. A row
    group's batches are merged/split to fill up to `batch_size` (`_SAMPLE_ROWS`),
    so the loop reliably stops after the first batch (rarely a second, for a
    file whose row groups are much smaller than the sample) — "roughly one row
    group's worth", never the whole object.

    `dtype_backend="pyarrow"` parity with the old whole-object read is kept by
    assembling the sample as a pyarrow `Table` and converting with
    `types_mapper=pd.ArrowDtype`, including the empty-file edge (an empty
    `Table` built straight from the footer's own field types, rather than an
    untyped `pd.DataFrame()`).

    The reader is opened with `chunk=STREAM_CHUNK`, not `RangeReader`'s default
    256 KiB seeking window. The default is sized for landing on a footer with a
    couple of small reads; `iter_batches` instead walks row groups sequentially
    (and, within a row group, jumps between the projected columns' — not
    necessarily contiguous — byte ranges), the same access pattern
    `csv_row_count` already uses `STREAM_CHUNK` for. On a file with many small
    row groups the seeking default turns into a storm of small range requests —
    measured at 6.8x the object's own size and 92 requests on a 200 row-group
    fixture. `STREAM_CHUNK` bounds that to a small, roughly constant number of
    requests (2-3, regardless of row-group count) with total bytes read landing
    close to the object's own size — not always strictly under it, since a
    single-window cache still re-fetches once a jump lands outside the current
    window, but never the unbounded multiplier a small window produces on an
    adversarial row-group layout.
    """
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
        # No requested column exists in the file — `profile_dataframe` reports
        # the missing names as a clean 422 off `columns`, not off this frame, so
        # what's returned here only needs to be an empty, columnless frame (the
        # same shape a whole-object `pd.read_parquet(path, columns=[])` gives).
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
        # A real, correctly-typed empty table (e.g. a header-only file) rather
        # than an untyped `pd.DataFrame(columns=present)` — matches what the old
        # whole-object `pd.read_parquet` produced for the same input.
        table = pa.table({c: pa.array([], type=schema.field(c).type) for c in present})
    # Parquet is already Arrow on disk; dtype_backend="pyarrow" keeps the buffers
    # zero-copy instead of materialising a numpy copy. The stat helpers + the
    # _to_native coercion are Arrow-scalar-safe (min/max → Python int/str,
    # timestamps → Timestamp.isoformat, NA dropped before reductions).
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
    Iceberg branch, so the profiler and the run path resolve the same table.

    Also mirrors that branch's `_str_or_none` fold: a `None`, empty, or
    whitespace-only `namespace` is not a real namespace and folds to the bare
    `table` — otherwise ``namespace=" "`` would yield `" .orders"` here while the
    run path resolves the bare `"orders"` for the same input (#721 code review)."""
    folded = namespace if isinstance(namespace, str) and namespace.strip() else None
    return f"{folded}.{table}" if folded else table


def _read_iceberg_dataframe(
    connection: Connection, *, identifier: str, columns: list[str], secret_store: SecretStore
) -> Any:
    """Resolve an Iceberg connection's config + optional secret (exactly as
    `build_iceberg_runner` does — the credential is optional), load the table
    ONCE, validate the requested `columns` against its schema *before any scan*
    (raising the same `ProfileColumnNotFoundError` the post-scan defence-in-depth
    check in `_profile_columns` raises — same message/detail shape — so an
    all-or-partially-invalid column list 422s without ever reading data), then
    materialise the already-loaded table as a projected, sampled DataFrame.

    The live I/O seam (catalog load + scan), monkeypatched in tests — the
    Iceberg analogue of the flat-file profiler's `_read_dataframe`. Before this
    fold, an all-invalid column list fell through to `read_iceberg_dataframe`'s
    ``selected_fields=("*",)`` fallback, scanning every column before the 422
    (#721 code review); that fallback now only fires for the `columns=None`
    (list-every-column) case, never reachable from here."""
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
    (metadata only, no data scan) — the Iceberg column-listing I/O seam."""
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
    """Column (header) names of a flat file on `connection` (ADLS Gen2 / S3).

    Reads only the header or the Parquet footer schema — and, since #882, only
    the *bytes* those need: Parquet lands on its footer through `RangeReader`,
    CSV takes a head range. This is called from the check editor on every target
    change, so downloading a multi-GB object to list column names was full egress
    and worker memory for a few hundred bytes of answer.

    Raises `ProfileTargetInvalidError` (422) for an unknown format and
    `ProfileFailedError` (502) if the file can't be read (exception not echoed).
    """
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
