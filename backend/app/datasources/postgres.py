"""PostgreSQL datasource (#1678) — one engine-generic adapter for any PostgreSQL server,
self-hosted or a managed service on any cloud. A `SqlEngineSpec` on the generic SQL base.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from backend.app.datasources.generic_sql import (
    ColumnCaps,
    GenericSqlConfig,
    SqlCatalog,
    SqlEngineSpec,
)

#: libpq ``sslmode`` values DataQ offers. ``require`` is the default: a connection that silently
#: downgrades to plaintext (libpq's own ``prefer``) is not offered at all, and ``disable`` must be
#: chosen explicitly.
PostgresSslMode = Literal["disable", "require", "verify-ca", "verify-full"]


class PostgresConfig(GenericSqlConfig):
    """Non-secret PostgreSQL connection config (the password comes from secrets)."""

    default_port: ClassVar[int] = 5432
    # NAMEDATALEN - 1: PostgreSQL truncates anything longer (with only a NOTICE).
    max_identifier_length: ClassVar[int] = 63

    sslmode: PostgresSslMode = "require"

    def engine_default_schema(self) -> str:
        return "public"


def _connect_args(config: PostgresConfig, timeout: int | None) -> dict[str, Any]:
    # `default_transaction_read_only` makes every transaction DataQ opens READ ONLY — the
    # server refuses a write however it was smuggled into a query. `search_path` pins the
    # connection's schema (quoted, so a mixed-case schema keeps its case) for the few paths that
    # address a target unqualified; the identifier allowlist on `schema` keeps it injection-free.
    # pg_catalog is named LAST so a user table can never be shadowed by a catalog relation of the
    # same name (unnamed, PostgreSQL searches it first).
    options = (
        "-c default_transaction_read_only=on"
        f' -c search_path="{config.default_schema}",pg_catalog'
    )
    args: dict[str, Any] = {
        "sslmode": config.sslmode,
        "options": options,
        "application_name": "dataq",
    }
    if config.sslmode in ("verify-ca", "verify-full"):
        # The OS trust store (libpq ≥ 16) — without it libpq looks for ~/.postgresql/root.crt,
        # which a container never has, and every verified connection would fail.
        args["sslrootcert"] = "system"
    if timeout is not None:
        args["connect_timeout"] = timeout
    return args


def _add_gx_datasource(
    context: Any, name: str, connection_string: str, engine_kwargs: dict[str, Any]
) -> Any:
    return context.data_sources.add_postgres(
        name=name, connection_string=connection_string, kwargs=engine_kwargs
    )


# pg_type.typcategory values whose types have MIN/MAX aggregates: numeric, string, datetime,
# timespan, enum (`min(anyenum)`), array (`min(anyarray)`) and network address. NOT boolean ('B'),
# nor 'U' — where json/jsonb/uuid live — nor geometric/bit-string: `min(jsonb)` is an error that
# would fail the whole profile, live-found on this adapter's first run.
_ORDERABLE_CATEGORIES = frozenset("NSDTEAI")
# Types with no equality operator: COUNT(DISTINCT) and GROUP BY refuse them.
_UNGROUPABLE_TYPES = frozenset({"json", "xml"})
_UNGROUPABLE_CATEGORIES = frozenset("G")

_COLUMN_TYPES_SQL = (
    "SELECT a.attname, t.typcategory, t.typname"
    " FROM pg_catalog.pg_attribute a"
    " JOIN pg_catalog.pg_class c ON c.oid = a.attrelid"
    " JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace"
    " JOIN pg_catalog.pg_type t ON t.oid = a.atttypid"
    " WHERE n.nspname = :schema AND c.relname = :table"
    " AND a.attnum > 0 AND NOT a.attisdropped"
)


def _column_caps(conn: Any, schema: str, table: str) -> dict[str, ColumnCaps]:
    from sqlalchemy import text

    # A domain carries its base type's category, so a domain over jsonb reads as jsonb does.
    rows = conn.execute(text(_COLUMN_TYPES_SQL), {"schema": schema, "table": table}).all()
    return {
        str(name): ColumnCaps(
            orderable=str(category) in _ORDERABLE_CATEGORIES,
            groupable=str(typname) not in _UNGROUPABLE_TYPES
            and str(category) not in _UNGROUPABLE_CATEGORIES,
        )
        for name, category, typname in rows
    }


# `has_*_privilege` filters to what the credential can actually use, the way Snowflake's
# INFORMATION_SCHEMA is privilege-filtered; partitions are left out (their parent is the table a
# check targets), and so are the catalog/toast/temp namespaces.
_SYSTEM_SCHEMA_FILTER = (
    "n.nspname NOT IN ('pg_catalog', 'information_schema')"
    " AND n.nspname NOT LIKE 'pg\\_toast%'"
    " AND n.nspname NOT LIKE 'pg\\_temp\\_%'"
    " AND has_schema_privilege(n.oid, 'USAGE')"
)

POSTGRES = SqlEngineSpec(
    conn_type="postgres",
    display_name="PostgreSQL",
    config_model=PostgresConfig,
    drivername="postgresql+psycopg2",
    gx_datasource=_add_gx_datasource,
    connect_args=_connect_args,
    namespace_scheme="postgres",
    database_in_name=True,
    catalog=SqlCatalog(
        # The f-strings below splice module CONSTANTS, never input; every value is bound.
        schemas_sql=(
            "SELECT n.nspname FROM pg_catalog.pg_namespace n"  # noqa: S608  # nosec B608
            f" WHERE {_SYSTEM_SCHEMA_FILTER}"
            " ORDER BY n.nspname LIMIT :lim"
        ),
        tables_sql=(
            "SELECT n.nspname, c.relname FROM pg_catalog.pg_class c"  # noqa: S608  # nosec B608
            " JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace"
            " WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f') AND NOT c.relispartition"
            f" AND {_SYSTEM_SCHEMA_FILTER}"
            " AND has_table_privilege(c.oid, 'SELECT')"
            " AND (CAST(:schema AS text) IS NULL OR n.nspname = :schema)"
            " ORDER BY n.nspname, c.relname LIMIT :lim"
        ),
    ),
    column_caps=_column_caps,
    # JSON cells stay the server's JSON TEXT on the run path. psycopg2 decodes json/jsonb
    # into dicts and lists, and GX's result formatting reads a dict cell as a multi-column
    # row — every failing column-map check on a JSON column then errored with "'list'
    # object has no attribute 'values'" (live-found on this adapter's first run).
    run_engine_options={"json_deserializer": str},
)
