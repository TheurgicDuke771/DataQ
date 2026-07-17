"""Unity Catalog warehouse-native lineage provider (#858, ADR 0034).

Reads ``system.access.table_lineage`` — Databricks' account-level lineage system table,
an **append-only event log** (every table read/write emits a row with an ``event_time``).
Verified against the real captured payload (2026-07-17 spike, 200 rows / 8 real edges).

Two facts drive the design, both from the real payload:

* **A lineage EDGE needs both endpoints.** Most rows have a NULL
  ``target_table_full_name`` (a pure read-access event — someone SELECTed a table, no
  write). Only rows with both ``source_table_full_name`` AND
  ``target_table_full_name`` are a table→table dependency. Path-based sources (an
  external file, ``source_type='PATH'`` with a NULL full name) are dropped — no table
  identity.
* **It is a LOG, so the refresh is INCREMENTAL and never prunes.** ``event_time`` is the
  watermark: read forward from the last persisted mark, upsert, return the new mark. An
  edge absent from the latest window is a historical fact, not a removed dependency —
  pruning it would erase real lineage. (Contrast Snowflake ``OBJECT_DEPENDENCIES``, a
  current-state view → snapshot-diff + prune.)

``*_table_full_name`` arrives as ``catalog.schema.table`` in UC's own **lower** case, so
:func:`asset_identity.format_unity_catalog_name` (which folds unquoted→lower, idempotent
here) rebuilds an identity byte-identical to a suite-resolved UC asset — no fold step
(the whole premise of warehouse-native lineage). The namespace is
``unitycatalog://{workspace host}`` from the connection's ``workspace_url`` — the exact
form `asset_identity._resolve_unity_catalog` produces.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import text

from backend.app.core.logging import get_logger
from backend.app.lineage.warehouse import (
    LineageEdgePair,
    LineageTier,
    WarehouseLineageResult,
    WarehouseLineageUnavailableError,
    dedupe_edges,
)
from backend.app.services.asset_identity import AssetIdentity, format_unity_catalog_name

log = get_logger(__name__)

# Endpoint types that carry a table identity. PATH (external file) / STREAMING_TABLE
# metadata without a full name are dropped — no asset identity.
_TABLE_TYPES = frozenset({"TABLE", "VIEW", "MATERIALIZED_VIEW", "STREAMING_TABLE"})

# Databricks' 0-argument default when no watermark is known: read the whole retention.
# system.access.table_lineage retains 365d, so a first pull is bounded regardless.
_DEFAULT_LOOKBACK_DAYS = 365


class UnityCatalogLineageProvider:
    """`WarehouseLineageProvider` for Unity Catalog via ``system.access.table_lineage``."""

    source = "unity_catalog"
    is_incremental = True

    def fetch_edges(
        self,
        conn: object,
        *,
        connection_config: dict[str, object],
        since: datetime | None = None,
    ) -> WarehouseLineageResult:
        namespace = self._namespace(connection_config)
        try:
            edges, new_watermark = self._from_table_lineage(conn, namespace, since)
        except Exception as exc:
            # A missing grant on system.access, or system tables not enabled, means we
            # learned nothing — Unavailable, so the refresh leaves the cache untouched.
            raise WarehouseLineageUnavailableError(
                "unity_catalog lineage unavailable: could not read "
                f"system.access.table_lineage ({type(exc).__name__}) — the SQL warehouse "
                "principal needs SELECT on system.access and system tables enabled"
            ) from exc
        return WarehouseLineageResult(
            edges=edges,
            tier=LineageTier.UNITY_CATALOG_SYSTEM_ACCESS,
            # The system table lags ingestion by up to ~1-2h (Databricks-documented).
            freshness_lag="~1-2h (system.access ingestion latency)",
            new_watermark=new_watermark,
        )

    # ── identity ──────────────────────────────────────────────────────────────
    def _namespace(self, config: dict[str, object]) -> str:
        workspace_url = config.get("workspace_url")
        if not isinstance(workspace_url, str) or not workspace_url.strip():
            raise WarehouseLineageUnavailableError(
                "unity_catalog lineage unavailable: connection config has no workspace_url"
            )
        # The same host derivation asset_identity uses (scheme-less tolerant), so the
        # namespace matches a suite-resolved UC asset byte-for-byte.
        parsed = urlparse(workspace_url)
        host = parsed.netloc or parsed.path.split("/", 1)[0]
        if not host:
            raise WarehouseLineageUnavailableError(
                "unity_catalog lineage unavailable: workspace_url has no host"
            )
        return f"unitycatalog://{host}"

    def _identity(self, namespace: str, catalog: str, schema: str, table: str) -> AssetIdentity:
        return AssetIdentity(
            namespace=namespace, name=format_unity_catalog_name(catalog, schema, table)
        )

    def _from_table_lineage(
        self, conn: Any, namespace: str, since: datetime | None
    ) -> tuple[tuple[LineageEdgePair, ...], datetime | None]:
        """Read forward from ``since`` (or the retention floor). Returns the edges plus
        the max ``event_time`` observed — the new watermark the caller persists. A pull
        with no new rows returns ``(dedupe([]), since)`` so the watermark never regresses.
        """
        # A concrete, BOUND floor (never a SQL expression as a param value): the caller's
        # watermark, or now-minus-retention on a first pull. event_time is compared with a
        # bound timestamp — no interpolation, no injection surface.
        floor = (
            since
            if since is not None
            else datetime.now(UTC) - timedelta(days=_DEFAULT_LOOKBACK_DAYS)
        )
        rows = conn.execute(
            text(
                "SELECT source_table_catalog, source_table_schema, source_table_name, "
                "target_table_catalog, target_table_schema, target_table_name, "
                "source_type, target_type, event_time "
                "FROM system.access.table_lineage "
                "WHERE source_table_full_name IS NOT NULL "
                "AND target_table_full_name IS NOT NULL "
                "AND event_time > :since "
                "ORDER BY event_time"
            ),
            {"since": floor},
        ).all()
        edges: list[LineageEdgePair] = []
        max_event_time = since
        for (
            src_cat,
            src_schema,
            src_name,
            tgt_cat,
            tgt_schema,
            tgt_name,
            src_type,
            tgt_type,
            event_time,
        ) in rows:
            if event_time is not None and (max_event_time is None or event_time > max_event_time):
                max_event_time = event_time
            if src_type not in _TABLE_TYPES or tgt_type not in _TABLE_TYPES:
                continue  # a PATH / non-table endpoint has no asset identity
            if not (src_cat and src_schema and src_name and tgt_cat and tgt_schema and tgt_name):
                continue  # a partial name can't form an identity
            edges.append(
                LineageEdgePair(
                    upstream=self._identity(namespace, src_cat, src_schema, src_name),
                    downstream=self._identity(namespace, tgt_cat, tgt_schema, tgt_name),
                )
            )
        return dedupe_edges(edges), max_event_time
