"""Amazon Redshift datasource (#1682) — provisioned clusters and Redshift Serverless workgroups, a
`SqlEngineSpec` on the generic SQL base (ADR 0045).

The driver is psycopg2 (already shipped for PostgreSQL) under the ``redshift+psycopg2`` dialect of
``sqlalchemy-redshift`` (MIT), which GX's Redshift datasource requires. Redshift speaks the
PostgreSQL wire protocol but not its catalog, which shapes this spec:

* **Read-only session, as on PostgreSQL.** The libpq startup ``options`` carry
  ``default_transaction_read_only`` and the ``search_path``; Redshift honours both (verified live).
* **Its own catalog queries.** ``pg_class`` has no ``relispartition`` and ``pg_type`` no
  ``typcategory``; a late-binding view has no ``pg_attribute`` rows. Tables come from the
  privilege-filtered ``information_schema.tables`` (which also hides a materialized view's internal
  ``mv_tbl__`` table), and columns — for the profiler and schema drift — from ``svv_columns``. A
  leader-node catalog cannot be joined to ``svv_mv_info``, so a materialized view lists as a view.
* **Names are lower case.** Redshift folds every identifier unless the cluster enables
  ``enable_case_sensitive_identifier``. That is also what makes it safe to hand GX the schema:
  without one, GX's Redshift column lookup matches the table name in every schema.
* **Trust is Amazon's CAs.** Every TLS mode verifies the chain against the Amazon root bundle the
  dialect ships (libpq treats ``require`` with a root file as ``verify-ca``).
"""

from __future__ import annotations

import re
from importlib.resources import files
from typing import Any, ClassVar

from backend.app.datasources.generic_sql import ColumnCaps, SqlCatalog, SqlEngineSpec
from backend.app.datasources.postgres import PostgresConfig

# `<cluster>.<id>.<region>.redshift.amazonaws.com` and
# `<workgroup>.<account>.<region>.redshift-serverless.amazonaws.com`.
_AWS_ENDPOINT = re.compile(
    r"(?P<name>[a-z0-9-]+)\.[a-z0-9]+\.(?P<region>[a-z]{2}(-[a-z]+)+-\d)"
    r"\.redshift(-serverless)?\.amazonaws\.com(\.cn)?"
)


class RedshiftConfig(PostgresConfig):
    """Non-secret Redshift connection config (the database user's password comes from secrets)."""

    default_port: ClassVar[int] = 5439
    max_identifier_length: ClassVar[int] = 127
    names_are_lower_case: ClassVar[bool] = True


def amazon_ca_bundle() -> str:
    return str(files("sqlalchemy_redshift").joinpath("redshift-ca-bundle.crt"))


def _connect_args(
    config: RedshiftConfig, timeout: int | None, *, read_only: bool = True
) -> dict[str, Any]:
    options = f"-c search_path=pg_catalog,{config.default_schema}"
    if config.default_schema != "public":
        options += ",public"
    if read_only:
        options = f"-c default_transaction_read_only=on {options}"
    args: dict[str, Any] = {
        "sslmode": config.sslmode,
        "options": options,
        "application_name": "dataq",
    }
    if config.sslmode != "disable":
        args["sslrootcert"] = amazon_ca_bundle()
    if timeout is not None:
        args["connect_timeout"] = timeout
    return args


def _add_gx_datasource(
    context: Any, name: str, connection_string: str, engine_kwargs: dict[str, Any]
) -> Any:
    return context.data_sources.add_redshift(
        name=name, connection_string=connection_string, kwargs=engine_kwargs
    )


def _namespace_authority(config: RedshiftConfig) -> str:
    """OpenLineage's Redshift authority, ``<cluster or workgroup>.<region>``, read from an AWS
    endpoint; any other host (a custom domain, an IP) is kept as configured."""
    host = config.host.lower()
    match = _AWS_ENDPOINT.fullmatch(host)
    if match is None:
        return host
    return f"{match['name']}.{match['region']}"


# Aggregates Redshift does not define (verified live): MIN/MAX over boolean and the spatial and
# sketch types; MIN(super) answers NULL whatever the data. No equality for spatial or sketch types.
_UNORDERABLE_TYPES = frozenset({"boolean", "super", "geometry", "geography", "hllsketch"})
_UNGROUPABLE_TYPES = frozenset({"geometry", "geography", "hllsketch"})


def _column_caps(conn: Any, schema: str, table: str) -> dict[str, ColumnCaps]:
    from sqlalchemy import text

    rows = conn.execute(
        text(
            "SELECT column_name, data_type FROM svv_columns"
            " WHERE table_schema = :schema AND table_name = :table"
        ),
        {"schema": schema, "table": table},
    ).all()
    return {
        str(name): ColumnCaps(
            orderable=str(data_type) not in _UNORDERABLE_TYPES,
            groupable=str(data_type) not in _UNGROUPABLE_TYPES,
        )
        for name, data_type in rows
    }


# Backslash is an escape character in a Redshift string literal, so the system-schema filter uses
# LEFT() rather than a LIKE with an escaped underscore.
_SYSTEM_SCHEMAS = "('information_schema', 'catalog_history')"

REDSHIFT = SqlEngineSpec(
    conn_type="redshift",
    display_name="Amazon Redshift",
    config_model=RedshiftConfig,
    drivername="redshift+psycopg2",
    gx_datasource=_add_gx_datasource,
    connect_args=_connect_args,
    namespace_scheme="redshift",
    namespace_authority=_namespace_authority,
    database_in_name=True,
    catalog=SqlCatalog(
        schemas_sql=(
            "SELECT n.nspname FROM pg_catalog.pg_namespace n"  # noqa: S608  # nosec B608
            f" WHERE n.nspname NOT IN {_SYSTEM_SCHEMAS} AND LEFT(n.nspname, 3) <> 'pg_'"
            " AND has_schema_privilege(n.oid, 'USAGE')"
            " ORDER BY n.nspname LIMIT :lim"
        ),
        tables_sql=(
            "SELECT table_schema, table_name, table_type FROM information_schema.tables"  # noqa: S608  # nosec B608
            f" WHERE table_schema NOT IN {_SYSTEM_SCHEMAS} AND LEFT(table_schema, 3) <> 'pg_'"
            " AND table_type IN ('BASE TABLE', 'VIEW')"
            " AND (CAST(:schema AS VARCHAR) IS NULL OR table_schema = :schema)"
            " ORDER BY table_schema, table_name LIMIT :lim"
        ),
    ),
    column_caps=_column_caps,
    gx_schema_with_session=True,
    columns_view="svv_columns",
)
