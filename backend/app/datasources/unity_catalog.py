"""Unity Catalog (Databricks) connection adapter.

A datasource (CLAUDE.md §4): DQ checks run against Unity Catalog tables via a
Databricks SQL Warehouse. Week 2 ships only the `ConnectionAdapter` seam (config
validation + connectivity `test`).

**Runner seam note:** the *check-run* path for UC must sit behind a
``UnityCatalogCheckRunner`` interface so v1.1 can swap GX for Databricks Labs DQX
on DLT/streaming (CLAUDE.md §5, ADR 0003). That runner is Week-3 work and is
deliberately **not** built here — this module is connection config + a
connectivity probe only.

Auth is a **personal access token (PAT)** — the v1 default, held in the
SecretStore (no credential-less mode, so none of the ADLS/S3 ``secret_ref``
nullability deferral applies). ``test`` opens a SQL-Warehouse connection and runs
``SELECT 1`` — a green test means the workspace + warehouse are reachable and the
PAT authenticates. ``databricks-sql-connector`` is imported lazily (per
``core/secrets.py``); like the other adapters it runs live and fails-soft pending
real credentials.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus, urlparse

import great_expectations as gx
from pydantic import BaseModel, ConfigDict, field_validator

from backend.app.core.secrets import SecretStore
from backend.app.datasources.base import CheckOutcome, CheckSpec, MonitorSpec, SuiteOutcome
from backend.app.datasources.gx_runner import run_expectations
from backend.app.datasources.monitors import run_monitors_over_engine


class UnityCatalogConfig(BaseModel):
    """Non-secret Databricks/UC connection config (the PAT comes from secrets).

    Maps from ``Connection.config``. ``workspace_url`` is the workspace root
    (e.g. ``https://adb-1234.5.azuredatabricks.net``); ``warehouse_id`` is the
    SQL Warehouse id, from which the connector's ``http_path`` is built. The PAT
    is resolved from the SecretStore at test time.
    """

    model_config = ConfigDict(extra="forbid")

    workspace_url: str
    warehouse_id: str

    @field_validator("workspace_url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("workspace_url must start with http:// or https://")
        return value.rstrip("/")

    @property
    def server_hostname(self) -> str:
        return urlparse(self.workspace_url).netloc

    @property
    def http_path(self) -> str:
        return f"/sql/1.0/warehouses/{self.warehouse_id}"


class UnityCatalogConnectionAdapter:
    """`ConnectionAdapter` for Unity Catalog — config validation + a SELECT 1 probe."""

    def validate_config(self, raw: dict[str, Any]) -> UnityCatalogConfig:
        return UnityCatalogConfig.model_validate(raw)

    def test(self, raw: dict[str, Any], secret: str, **_: Any) -> None:
        """Open a SQL-Warehouse connection and run ``SELECT 1``; raise on failure.

        ``secret`` is the Databricks PAT. Deliberately GX/DQX-free — a lightweight
        connectivity probe, not a suite run.
        """
        from databricks import sql

        config = self.validate_config(raw)
        # databricks-sql-connector is only partially typed; treat the connection
        # as dynamic so strict mypy doesn't flag no-untyped-call on its methods.
        connection: Any = sql.connect(
            server_hostname=config.server_hostname,
            http_path=config.http_path,
            access_token=secret,
        )
        try:
            cursor = connection.cursor()
            try:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            finally:
                cursor.close()
        finally:
            connection.close()


def build_databricks_url(
    config: UnityCatalogConfig, token: str, *, catalog: str | None = None
) -> str:
    """SQLAlchemy URL for the Databricks SQL Warehouse (databricks dialect).

    The PAT, http_path and `catalog` are URL-encoded. Pinning `catalog` sets the
    session default so a 2-level `schema.table` reference resolves to
    `catalog.schema.table`; the profiler leaves it unset and qualifies the
    namespace in the query instead.
    """
    url = (
        f"databricks://token:{quote_plus(token)}@{config.server_hostname}"
        f"?http_path={quote_plus(config.http_path)}"
    )
    if catalog:
        url += f"&catalog={quote_plus(catalog)}"
    return url


class UnityCatalogCheckRunner:
    """GX `CheckRunner` for Unity Catalog via the Databricks SQL Warehouse.

    The UC run path reads the target table into a pandas DataFrame and validates
    that frame with GX — the "GX DataFrame datasource" shape (CLAUDE.md §5), the
    same shape Databricks Labs DQX consumes, so v1.1 can swap GX for DQX behind
    this same interface without touching the suite/check/result layer.

    `table` + `schema` come from `run_checks` (the suite's target); `catalog` is
    fixed per run (held here). Reflecting + reading the table is the live seam
    (`_read_table`), monkeypatched in tests; GX then runs in-process on the
    returned frame, so the validation path itself is fully covered.
    """

    def __init__(self, *, config: UnityCatalogConfig, token: str, catalog: str) -> None:
        self._config = config
        self._token = token
        self._catalog = catalog
        self._engine: Any | None = None

    def _get_engine(self) -> Any:
        """The runner's ONE lazily-built engine (#427), shared by the GX read
        (`_read_table`) AND `run_monitors` — a mixed suite (expectations +
        monitors) now pays a single warehouse session instead of two. Disposed by
        `close()`; the run path owns that lifecycle."""
        if self._engine is None:
            from sqlalchemy import create_engine

            self._engine = create_engine(
                build_databricks_url(self._config, self._token, catalog=self._catalog)
            )
        return self._engine

    def close(self) -> None:
        """Dispose the shared engine's pool. Idempotent; a no-op if never used."""
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None

    def _read_table(self, *, table: str, schema: str | None) -> Any:
        """Reflect + read the whole table into a DataFrame (live seam).

        `read_sql_table` reflects through SQLAlchemy (proper dialect quoting), so
        the table/schema identifiers are never string-formatted into SQL; the
        pinned catalog + `schema` qualify it to `catalog.schema.table`.
        """
        import pandas as pd

        return pd.read_sql_table(table, self._get_engine(), schema=schema)

    def run_checks(
        self,
        *,
        table: str,
        schema: str | None,
        checks: list[CheckSpec],
        index_columns: list[str] | None = None,
    ) -> SuiteOutcome:
        df = self._read_table(table=table, schema=schema)
        context = gx.get_context(mode="ephemeral")
        asset = context.data_sources.add_pandas(name="uc").add_dataframe_asset(name="table")
        batch_definition = asset.add_batch_definition_whole_dataframe(name="whole_dataframe")
        return run_expectations(
            context,
            batch_definition=batch_definition,
            checks=checks,
            name="suite-uc",
            batch_parameters={"dataframe": df},
            index_columns=index_columns,
        )

    def run_monitors(
        self, *, table: str, schema: str | None, monitors: list[MonitorSpec]
    ) -> list[CheckOutcome]:
        """Evaluate freshness/volume monitors via scalar SQL aggregates over the SQL
        Warehouse (no GX / no DataFrame read), over the runner's shared engine
        (#427 — one connection per run, no per-call engine). The pinned
        ``catalog`` qualifies the target as ``catalog.schema.table``. A connection
        failure propagates; a bad monitor errors only itself."""
        return run_monitors_over_engine(
            self._get_engine(),
            table=table,
            schema=schema,
            catalog=self._catalog,
            monitors=monitors,
        )


def build_unity_catalog_runner(
    *, config: dict[str, Any], secret_ref: str | None, secret_store: SecretStore, catalog: str
) -> UnityCatalogCheckRunner:
    """Build a runner from a UC `Connection`'s primitives + the target `catalog`.

    Mirrors `build_snowflake_runner`: resolves the PAT eagerly and takes the raw
    config dict (not the ORM model) to keep the adapter decoupled from `db/`.
    """
    if not secret_ref:
        raise ValueError("Unity Catalog connection requires secret_ref for the PAT")
    uc_config = UnityCatalogConfig.model_validate(config)
    token = secret_store.get(secret_ref)
    return UnityCatalogCheckRunner(config=uc_config, token=token, catalog=catalog)
