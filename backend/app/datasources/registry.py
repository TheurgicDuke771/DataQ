"""Connection-type → adapter + runner registry.

The single place that maps a ``Connection.type`` to its `ConnectionAdapter`
(`get_connection_adapter`, all six types) and — for datasources only — to its
`CheckRunner` builder (`build_check_runner`). Service/worker code dispatches
through these and never branches on ``connection.type`` itself; adding a
datasource is an entry here plus the adapter/runner, nothing else.
"""

from __future__ import annotations

from typing import Any, Protocol

from backend.app.core.secrets import SecretStore
from backend.app.datasources.adls import AdlsConnectionAdapter
from backend.app.datasources.base import CheckRunner, ConnectionAdapter
from backend.app.datasources.flatfile import build_flatfile_runner
from backend.app.datasources.s3 import S3ConnectionAdapter
from backend.app.datasources.snowflake import SnowflakeConnectionAdapter, build_snowflake_runner
from backend.app.datasources.unity_catalog import (
    UnityCatalogConnectionAdapter,
    build_unity_catalog_runner,
)
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


# ───────────────────────── CheckRunner registry ─────────────────────
#
# Only datasources get a runner (orchestration providers are absent → asking for
# their runner raises). Each builder is normalised to one signature so the worker
# can dispatch through `build_check_runner` without branching on the type. The
# underlying `build_*` take primitives (not the ORM `Connection`) to keep the
# adapters decoupled from `db/`; the caller unpacks the row.


class _RunnerBuilder(Protocol):
    def __call__(
        self,
        *,
        conn_type: str,
        config: dict[str, Any],
        secret_ref: str | None,
        secret_store: SecretStore,
        catalog: str | None,
    ) -> CheckRunner: ...


def _snowflake_runner(
    *, config: dict[str, Any], secret_ref: str | None, secret_store: SecretStore, **_: Any
) -> CheckRunner:
    return build_snowflake_runner(config=config, secret_ref=secret_ref, secret_store=secret_store)


def _flatfile_runner(
    *,
    conn_type: str,
    config: dict[str, Any],
    secret_ref: str | None,
    secret_store: SecretStore,
    **_: Any,
) -> CheckRunner:
    return build_flatfile_runner(
        conn_type=conn_type, config=config, secret_ref=secret_ref, secret_store=secret_store
    )


def _unity_catalog_runner(
    *,
    config: dict[str, Any],
    secret_ref: str | None,
    secret_store: SecretStore,
    catalog: str | None,
    **_: Any,
) -> CheckRunner:
    if not catalog:
        raise UnsupportedConnectionTypeError("Unity Catalog run requires a catalog")
    return build_unity_catalog_runner(
        config=config, secret_ref=secret_ref, secret_store=secret_store, catalog=catalog
    )


_RUNNER_BUILDERS: dict[str, _RunnerBuilder] = {
    "snowflake": _snowflake_runner,
    "adls_gen2": _flatfile_runner,
    "s3": _flatfile_runner,
    "unity_catalog": _unity_catalog_runner,
}


def build_check_runner(
    *,
    conn_type: str,
    config: dict[str, Any],
    secret_ref: str | None,
    secret_store: SecretStore,
    catalog: str | None = None,
) -> CheckRunner:
    """Build the `CheckRunner` for ``conn_type`` from a connection's primitives.

    Dispatches by type to the registered builder. Raises
    `UnsupportedConnectionTypeError` for a type with no runner (e.g. an
    orchestration provider, or Unity Catalog without a ``catalog``).
    """
    builder = _RUNNER_BUILDERS.get(conn_type)
    if builder is None:
        raise UnsupportedConnectionTypeError(f"No check runner registered for type {conn_type!r}")
    return builder(
        conn_type=conn_type,
        config=config,
        secret_ref=secret_ref,
        secret_store=secret_store,
        catalog=catalog,
    )
