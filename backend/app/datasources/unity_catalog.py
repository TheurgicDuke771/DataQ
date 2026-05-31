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
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, field_validator


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

    def test(self, raw: dict[str, Any], secret: str) -> None:
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
