"""Connection-type → adapter registry.

The single place that maps a ``Connection.type`` to its `ConnectionAdapter`.
Adding a datasource type (ADF, ADLS Gen2, S3, Unity Catalog — Weeks 2-3) is a
one-line entry here plus the adapter itself; connection-CRUD service code stays
untouched, dispatching through `get_connection_adapter`.
"""

from __future__ import annotations

from backend.app.datasources.base import ConnectionAdapter
from backend.app.datasources.snowflake import SnowflakeConnectionAdapter
from backend.app.orchestration.adf import ADFConnectionAdapter


class UnsupportedConnectionTypeError(ValueError):
    """Raised when no adapter is registered for a connection type."""


# Datasource and orchestration-provider connection types share this one registry
# (both implement the `ConnectionAdapter` seam); the run path keeps them apart —
# only datasources get a `CheckRunner`. ADF is an orchestration provider, so its
# adapter lives under `orchestration/`, not `datasources/` (CLAUDE.md §4).
_ADAPTERS: dict[str, ConnectionAdapter] = {
    "snowflake": SnowflakeConnectionAdapter(),
    "adf": ADFConnectionAdapter(),
}


def get_connection_adapter(conn_type: str) -> ConnectionAdapter:
    adapter = _ADAPTERS.get(conn_type)
    if adapter is None:
        raise UnsupportedConnectionTypeError(
            f"No connection adapter registered for type {conn_type!r}"
        )
    return adapter
