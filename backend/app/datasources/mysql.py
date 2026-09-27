"""MySQL / MariaDB datasource (#1684) — one engine-generic adapter for any MySQL-protocol server,
self-hosted or managed. A `SqlEngineSpec` on the generic SQL base (ADR 0045).

The driver is **PyMySQL** (MIT). `mysqlclient` and `mysql-connector-python` are GPL-licensed and
therefore excluded by the project's licence rule (ADR 0031) — do not swap either in.
"""

from __future__ import annotations

import ssl
from typing import Any, ClassVar

from backend.app.datasources.generic_sql import GenericSqlConfig, SqlCatalog, SqlEngineSpec

# Schemas that are the server's own bookkeeping, never a user's table.
_SYSTEM_SCHEMAS = "('mysql', 'sys', 'performance_schema', 'information_schema')"


class MySqlConfig(GenericSqlConfig):
    """Non-secret MySQL / MariaDB connection config (the password comes from secrets).

    A MySQL *schema* IS a database, so the optional ``schema`` is only an alternative default
    database for unqualified targets; it defaults to ``database``.
    """

    default_port: ClassVar[int] = 3306
    max_identifier_length: ClassVar[int] = 64

    def engine_default_schema(self) -> str:
        return self.database

    def url_database(self) -> str:
        # The session's default database is how a run is scoped to its target schema.
        return self.default_schema


def _tls(config: MySqlConfig) -> dict[str, Any]:
    """PyMySQL's TLS arguments for a mode. Every mode but ``disable`` REQUIRES TLS: PyMySQL
    upgrades opportunistically when given no TLS options at all (the silent downgrade DataQ does
    not offer), so ``require`` passes an explicit no-verify context rather than nothing.
    """
    if config.sslmode == "disable":
        return {"ssl_disabled": True}
    context = ssl.create_default_context()  # the system trust store
    if config.sslmode == "require":
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    elif config.sslmode == "verify-ca":
        context.check_hostname = False
    return {"ssl": context}


def _connect_args(
    config: MySqlConfig, timeout: int | None, *, read_only: bool = True
) -> dict[str, Any]:
    # The session's own settings are `_session_statements`, not an `init_command`: PyMySQL runs
    # exactly one init statement and the two settings cannot share one.
    args: dict[str, Any] = {"charset": "utf8mb4", **_tls(config)}
    if timeout is not None:
        args["connect_timeout"] = timeout
    return args


def _session_statements(read_only: bool) -> tuple[str, ...]:
    # `time_zone` UTC so a TIMESTAMP column comes back in UTC (the driver returns naive datetimes,
    # which the freshness math reads as UTC). READ ONLY for every transaction the session opens —
    # the server refuses a write however it was smuggled into a query. Spelled as the
    # `SET SESSION TRANSACTION` statement, which every MySQL and MariaDB version accepts: the
    # `transaction_read_only` VARIABLE does not exist before MariaDB 11.1 (live-found on 10.6 —
    # every connection failed 1193), and `tx_read_only` is gone from MySQL 8.
    statements: tuple[str, ...] = ("SET SESSION time_zone = '+00:00'",)
    if read_only:
        statements += ("SET SESSION TRANSACTION READ ONLY",)
    return statements


def _add_gx_datasource(
    context: Any, name: str, connection_string: str, engine_kwargs: dict[str, Any]
) -> Any:
    return context.data_sources.add_sql(
        name=name, connection_string=connection_string, kwargs=engine_kwargs
    )


MYSQL = SqlEngineSpec(
    conn_type="mysql",
    display_name="MySQL",
    config_model=MySqlConfig,
    drivername="mysql+pymysql",
    gx_datasource=_add_gx_datasource,
    connect_args=_connect_args,
    namespace_scheme="mysql",
    database_in_name=False,
    catalog=SqlCatalog(
        # information_schema is privilege-filtered on both MySQL and MariaDB: a schema or table the
        # credential has no privilege on is simply absent. The f-strings splice a module constant.
        schemas_sql=(
            "SELECT schema_name FROM information_schema.schemata"  # noqa: S608  # nosec B608
            f" WHERE schema_name NOT IN {_SYSTEM_SCHEMAS}"
            " ORDER BY schema_name LIMIT :lim"
        ),
        tables_sql=(
            "SELECT table_schema, table_name FROM information_schema.tables"  # noqa: S608  # nosec B608
            f" WHERE table_schema NOT IN {_SYSTEM_SCHEMAS}"
            " AND table_type IN ('BASE TABLE', 'VIEW', 'SYSTEM VERSIONED')"
            " AND (:schema IS NULL OR table_schema = :schema)"
            " ORDER BY table_schema, table_name LIMIT :lim"
        ),
    ),
    # No `run_engine_options`: PyMySQL hands a JSON cell back as its text (unlike psycopg2), so
    # GX's result formatting never sees a dict — verified live on MySQL 8.4 and MariaDB 11.8.
    # GX's MySQL uniqueness check copies the column into two session TEMPORARY tables (its
    # workaround for a temp-table limitation that does not apply to the base tables DataQ reads).
    # A read-only transaction refuses that, so this one type runs on its own session without
    # the guard — which also needs the CREATE TEMPORARY TABLES grant (live-found).
    temp_table_types=frozenset({"expect_column_values_to_be_unique"}),
    session_statements=_session_statements,
)
