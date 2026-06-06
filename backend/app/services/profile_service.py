"""Column profiler — per-column statistics for the check editor.

Given a table and a set of columns on a suite's connection, compute the stats
an author needs before writing expectations: row count, null count / fraction,
distinct count, min / max, and the most frequent values. Persists nothing — it
is a read-only authoring aid (the check-editor "profile on table select" panel).

v1 limit: only Snowflake (the connection-type dispatch generalises in Week 5,
ADR 0011); other types get a clear 422. Flat-file (Pandas) and Unity Catalog
profilers are the sibling Week-3 tasks.

**SQL-injection safety.** Table / schema / column names are caller-supplied and
become SQL *identifiers* (they can't be bound parameters). Queries are built with
the SQLAlchemy Core expression language (`select` / `table` / `column`) — never
string formatting — so the dialect does the quoting and there is no raw-SQL sink.
As defence-in-depth (and for a clean early 422) each identifier is additionally
validated against a strict allowlist before it reaches a `column()` / `table()`.
Statistic columns use positional labels (`nulls_0`, …) so the column name never
has to round-trip through a label.

Like the GX adapter, the pure pieces (identifier validation, query building,
result assembly) are unit-testable without a warehouse; the one I/O seam
(`_open_connection`) is monkeypatched in tests, and a live smoke is deferred.
"""

from __future__ import annotations

import re
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy import column, distinct, func, select, table
from sqlalchemy.sql import Select

from backend.app.core.errors import DataQError
from backend.app.core.jsonsafe import sanitize_json
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.datasources.snowflake import SnowflakeConfig, build_connection_string
from backend.app.db.models import Connection

log = get_logger(__name__)

_SUPPORTED_TYPES = {"snowflake"}

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


class ProfileIdentifierInvalidError(DataQError):
    status_code = 422
    code = "profile_identifier_invalid"


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
class TableProfile:
    table: str
    schema: str
    row_count: int
    columns: list[ColumnProfile]


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


def _table(schema: str, table_name: str) -> Any:
    return table(validate_identifier(table_name), schema=validate_identifier(schema))


def build_aggregate_query(schema: str, table_name: str, columns: list[str]) -> Select[Any]:
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
    return select(*projection).select_from(_table(schema, table_name))


def build_top_values_query(schema: str, table_name: str, col: str, top_n: int) -> Select[Any]:
    """Most frequent non-null values for one column (highest count first)."""
    c: Any = column(validate_identifier(col))
    freq = func.count().label("freq")
    return (
        select(c.label("value"), freq)
        .select_from(_table(schema, table_name))
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
) -> TableProfile:
    """Build the `TableProfile` from raw query rows (pure, warehouse-free)."""
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
    return TableProfile(table=table, schema=schema, row_count=row_count, columns=profiles)


# ───────────────────────── I/O seam (monkeypatched in tests) ────────


@contextmanager
def _open_connection(connection: Connection, secret_store: SecretStore) -> Generator[Any]:
    """Yield a live SQLAlchemy connection to the datasource, disposing the engine."""
    from sqlalchemy import create_engine

    if not connection.secret_ref:
        raise ValueError("connection requires secret_ref for the password")
    config = SnowflakeConfig.model_validate(connection.config)
    password = secret_store.get(connection.secret_ref)
    engine = create_engine(
        build_connection_string(config, password),
        connect_args={"login_timeout": _LOGIN_TIMEOUT, "network_timeout": _NETWORK_TIMEOUT},
    )
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
) -> TableProfile:
    """Profile `columns` of `table` on `connection`.

    Raises `ProfileUnsupportedError` (422) for a non-Snowflake connection,
    `ProfileIdentifierInvalidError` (422) for a bad table/schema/column name
    (validated *before* any query runs), and `ProfileFailedError` (502) if the
    profile can't execute — the adapter exception is never echoed (it can carry
    DSN/credential fragments).
    """
    if connection.type not in _SUPPORTED_TYPES:
        raise ProfileUnsupportedError(
            f"column profiling is not supported for {connection.type!r} connections in v1",
            detail={"type": connection.type, "supported": sorted(_SUPPORTED_TYPES)},
        )
    effective_schema = schema if schema is not None else connection.config.get("schema")
    if not isinstance(effective_schema, str):
        raise ProfileIdentifierInvalidError(
            "no schema given and the connection has none", detail={"schema": effective_schema}
        )
    # Validate every identifier up front (422) before any query is built/run.
    validate_identifier(table)
    validate_identifier(effective_schema)
    for col in columns:
        validate_identifier(col)

    try:
        with _open_connection(connection, secret_store) as conn:
            aggregate = (
                conn.execute(build_aggregate_query(effective_schema, table, columns))
                .mappings()
                .one()
            )
            top_values = {
                col: list(
                    conn.execute(
                        build_top_values_query(effective_schema, table, col, top_n)
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
        columns=columns,
        aggregate=aggregate,
        top_values=top_values,
    )
