"""Connection-type → adapter registry.

The single place that maps a ``Connection.type`` to its `ConnectionAdapter`.
Adding a datasource type (ADF, ADLS Gen2, S3, Unity Catalog — Weeks 2-3) is a
one-line entry here plus the adapter itself; connection-CRUD service code stays
untouched, dispatching through `get_connection_adapter`.
"""

from __future__ import annotations

from backend.app.datasources.adls import AdlsConnectionAdapter
from backend.app.datasources.base import ConnectionAdapter
from backend.app.datasources.s3 import S3ConnectionAdapter
from backend.app.datasources.snowflake import SnowflakeConnectionAdapter
from backend.app.datasources.unity_catalog import UnityCatalogConnectionAdapter
from backend.app.orchestration.adf import ADFConnectionAdapter
from backend.app.orchestration.airflow import AirflowConnectionAdapter


class UnsupportedConnectionTypeError(ValueError):
    """Raised when no adapter is registered for a connection type."""


# Datasource and orchestration-provider connection types share this one registry
# (both implement the `ConnectionAdapter` seam); the run path keeps them apart —
# only datasources get a `CheckRunner`. ADF and Airflow are orchestration
# providers, so their adapters live under `orchestration/`, not `datasources/`
# (CLAUDE.md §4).
_ADAPTERS: dict[str, ConnectionAdapter] = {
    "snowflake": SnowflakeConnectionAdapter(),
    "adls_gen2": AdlsConnectionAdapter(),
    "s3": S3ConnectionAdapter(),
    "unity_catalog": UnityCatalogConnectionAdapter(),
    "adf": ADFConnectionAdapter(),
    "airflow": AirflowConnectionAdapter(),
}


def get_connection_adapter(conn_type: str) -> ConnectionAdapter:
    adapter = _ADAPTERS.get(conn_type)
    if adapter is None:
        raise UnsupportedConnectionTypeError(
            f"No connection adapter registered for type {conn_type!r}"
        )
    return adapter
