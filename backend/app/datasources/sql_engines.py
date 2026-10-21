"""The registry of engines on the generic SQL base (#1678).

Adding an engine is one `SqlEngineSpec` module plus one entry here; every capability set that
names SQL datasources (custom SQL, monitors, pushdown admission, profiling, schema drift, the
inventory sync, browsing) derives from `GENERIC_SQL_TYPES` rather than restating it. Kept free of
GX and SQLAlchemy imports so the author-time gates can import it at module scope.
"""

from __future__ import annotations

from typing import Any

from backend.app.datasources.athena import ATHENA
from backend.app.datasources.bigquery import BIGQUERY
from backend.app.datasources.generic_sql import (
    GenericSqlConfig,
    NoDefaultSchemaError,
    SqlEngineSpec,
)
from backend.app.datasources.mssql import MSSQL
from backend.app.datasources.mysql import MYSQL
from backend.app.datasources.postgres import POSTGRES
from backend.app.datasources.redshift import REDSHIFT
from backend.app.datasources.trino import TRINO

SQL_ENGINES: dict[str, SqlEngineSpec] = {
    spec.conn_type: spec for spec in (POSTGRES, MYSQL, TRINO, MSSQL, ATHENA, REDSHIFT, BIGQUERY)
}

GENERIC_SQL_TYPES: frozenset[str] = frozenset(SQL_ENGINES)

# Connection types whose CheckRunner evaluates ordinary expectations on a SQL batch. Unity Catalog
# is deliberately absent: its pushdown set is an allowlist, so anything outside it — including
# every `DATAFRAME_ONLY_EXPECTATION_TYPES` entry — routes to that runner's pandas batch. Lives
# here, not in `gx_runner`, which the generic runner imports (an import cycle otherwise).
SQL_BATCH_CONNECTION_TYPES: frozenset[str] = frozenset({"snowflake", *GENERIC_SQL_TYPES})


def sql_engine(conn_type: str) -> SqlEngineSpec | None:
    """The spec for a generic SQL connection type, or ``None`` for any other type."""
    return SQL_ENGINES.get(conn_type)


def default_schema(conn_type: str, config: dict[str, Any]) -> str | None:
    """The schema an unqualified target resolves in on a generic SQL connection, or ``None``
    when ``conn_type`` is not one or the connection has no default (the caller then applies its
    own rule).
    """
    spec = SQL_ENGINES.get(conn_type)
    if spec is None:
        return None
    validated: GenericSqlConfig = spec.validate_config(config)
    try:
        return validated.default_schema
    except NoDefaultSchemaError:
        return None
