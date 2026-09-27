"""Table enumeration for the generic SQL engines (#1678, ADR 0040).

The inventory half of the ADR 0040 seam only: these engines expose no lineage log DataQ reads,
so they are NOT warehouse-lineage sources (`WAREHOUSE_LINEAGE_CONNECTION_TYPES`) and an asset on
one says its lineage is unknown rather than empty.
"""

from __future__ import annotations

from backend.app.datasources.generic_sql import SqlEngineSpec, table_rows
from backend.app.services.asset_identity import AssetIdentity


class GenericSqlTableEnumerator:
    """`TableEnumerator` over one generic SQL engine's catalog query (`SqlCatalog.tables_sql`)."""

    def __init__(self, spec: SqlEngineSpec) -> None:
        self.spec = spec
        self.source = spec.conn_type

    def enumerate_tables(
        self,
        conn: object,
        *,
        connection_config: dict[str, object],
        limit: int | None = None,
    ) -> tuple[AssetIdentity, ...]:
        config = self.spec.validate_config(dict(connection_config))
        namespace = self.spec.namespace(config)
        return tuple(
            AssetIdentity(
                namespace=namespace,
                name=self.spec.asset_name(config, schema=schema, table=table),
            )
            for schema, table in table_rows(self.spec, conn, limit=limit)
        )
