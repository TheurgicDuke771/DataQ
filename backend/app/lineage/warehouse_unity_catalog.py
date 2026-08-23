"""Unity Catalog warehouse-native lineage provider (#858, ADR 0034)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import text

from backend.app.core.logging import get_logger
from backend.app.lineage.warehouse import (
    MAX_COLUMN_PAIRS_PER_EDGE,
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

# Safety re-scan window subtracted from the persisted watermark on each incremental pull.
# system.access.table_lineage ingests with ~1-2h lag.
_WATERMARK_SAFETY = timedelta(hours=6)

# The shared defensive cap (#901/#908, lives in `warehouse`) — pairs are collected in
# event order and the cap keeps the first N distinct.
_MAX_COLUMN_PAIRS_PER_EDGE = MAX_COLUMN_PAIRS_PER_EDGE


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
        # Column grain (#901): a refinement of the table edges, never a reason to fail them — a
        # workspace where column_lineage is gated separately still gets table lineage.
        degraded_reason: str | None = None
        try:
            edges = self._attach_column_pairs(conn, edges, since)
        except Exception as exc:
            degraded_reason = (
                "column-level lineage unavailable: could not read "
                f"system.access.column_lineage ({type(exc).__name__})"
            )
            log.warning(
                "warehouse_lineage_column_grain_failed",
                source=self.source,
                error_type=type(exc).__name__,
            )
        return WarehouseLineageResult(
            edges=edges,
            tier=LineageTier.UNITY_CATALOG_SYSTEM_ACCESS,
            degraded_reason=degraded_reason,
            # The system table lags ingestion by up to ~1-2h (Databricks-documented).
            freshness_lag="~1-2h (system.access ingestion latency)",
            new_watermark=new_watermark,
        )

    # ── identity ──────────────────────────────────────────────────────────────
    def enumerate_tables(
        self,
        conn: object,
        *,
        connection_config: dict[str, object],
        limit: int | None = None,
    ) -> tuple[AssetIdentity, ...]:
        """ADR 0040 — enumerate the workspace's tables from ``system.information_schema.tables``."""
        namespace = self._namespace(connection_config)
        sql = (
            "SELECT table_catalog, table_schema, table_name"
            " FROM system.information_schema.tables"
            " WHERE table_catalog IS NOT NULL AND table_schema IS NOT NULL"
            " AND table_name IS NOT NULL"
            " AND table_schema != 'information_schema'"
            " AND table_catalog NOT IN ('system', 'samples', '__databricks_internal')"
            " AND table_type IN ('MANAGED', 'EXTERNAL', 'VIEW', 'MATERIALIZED_VIEW',"
            " 'STREAMING_TABLE')"
            " ORDER BY table_catalog, table_schema, table_name"
        )
        params: dict[str, object] = {}
        if limit is not None:
            sql += " LIMIT :lim"
            params["lim"] = int(limit)
        rows = conn.execute(text(sql), params).all()  # type: ignore[attr-defined]
        return tuple(
            self._identity(namespace, catalog, schema, table)
            for catalog, schema, table in rows
            if catalog and schema and table  # same NULL-row guard as the SF seam
        )

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
        # A concrete, BOUND floor (never a SQL expression as a param value): event_time is compared
        # with a bound timestamp — no interpolation, no injection surface.
        floor = (
            since - _WATERMARK_SAFETY
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

    def _attach_column_pairs(
        self,
        conn: Any,
        edges: tuple[LineageEdgePair, ...],
        since: datetime | None,
    ) -> tuple[LineageEdgePair, ...]:
        """Refine the table edges with ``system.access.column_lineage`` pairs (#901)."""
        if not edges:
            return edges
        floor = (
            since - _WATERMARK_SAFETY
            if since is not None
            else datetime.now(UTC) - timedelta(days=_DEFAULT_LOOKBACK_DAYS)
        )
        rows = conn.execute(
            text(
                "SELECT source_table_catalog, source_table_schema, source_table_name, "
                "source_column_name, "
                "target_table_catalog, target_table_schema, target_table_name, "
                "target_column_name "
                "FROM system.access.column_lineage "
                "WHERE source_table_full_name IS NOT NULL "
                "AND target_table_full_name IS NOT NULL "
                "AND source_column_name IS NOT NULL "
                "AND target_column_name IS NOT NULL "
                "AND event_time > :since"
            ),
            {"since": floor},
        ).all()
        by_table_pair: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for src_cat, src_schema, src_name, src_col, tgt_cat, tgt_schema, tgt_name, tgt_col in rows:
            if not (src_cat and src_schema and src_name and tgt_cat and tgt_schema and tgt_name):
                continue  # a partial name can't form an identity (mirrors the table pull)
            key = (
                format_unity_catalog_name(str(src_cat), str(src_schema), str(src_name)),
                format_unity_catalog_name(str(tgt_cat), str(tgt_schema), str(tgt_name)),
            )
            pair = (str(src_col), str(tgt_col))
            bucket = by_table_pair.setdefault(key, [])
            if pair not in bucket and len(bucket) < _MAX_COLUMN_PAIRS_PER_EDGE:
                bucket.append(pair)
        matched = 0
        refined: list[LineageEdgePair] = []
        for edge in edges:
            pairs = by_table_pair.pop((edge.upstream.name, edge.downstream.name), None)
            if pairs:
                matched += 1
                refined.append(
                    LineageEdgePair(
                        upstream=edge.upstream,
                        downstream=edge.downstream,
                        column_pairs=tuple(sorted(pairs)),
                    )
                )
            else:
                refined.append(edge)
        if by_table_pair:
            # Column events whose table pair has no edge in this window — expected when
            # the two logs' ingestion isn't aligned; the pairs return on a later pull.
            log.info(
                "warehouse_lineage_column_pairs_unanchored",
                source=self.source,
                table_pairs=len(by_table_pair),
            )
        log.info(
            "warehouse_lineage_column_grain",
            source=self.source,
            edges_with_columns=matched,
        )
        return tuple(refined)
