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

# Safety re-scan window subtracted from the persisted watermark on each incremental
# pull. system.access.table_lineage ingests with ~1-2h lag, so a statement whose
# event_time is <= the last watermark can be INGESTED after the pull that set it — a
# strict `> watermark` would miss it forever. Re-reading a bounded window before the
# watermark closes that gap; the edge upsert is idempotent (ON CONFLICT bumps
# last_seen), so re-reads are harmless. 6h comfortably exceeds the documented lag.
_WATERMARK_SAFETY = timedelta(hours=6)

# Defensive per-edge cap on persisted column pairs (#901): real schemas are bounded,
# but a generated/exploded join must not balloon the edge's JSONB. Deterministic —
# pairs are collected in event order and the cap keeps the first N distinct.
_MAX_COLUMN_PAIRS_PER_EDGE = 500


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
        # Column grain (#901): a refinement of the table edges, never a reason to fail
        # them — a workspace where column_lineage is gated separately still gets table
        # lineage, with an honest degrade note instead of a silent absence.
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
        # A concrete, BOUND floor (never a SQL expression as a param value): event_time
        # is compared with a bound timestamp — no interpolation, no injection surface.
        # An incremental pull re-scans a safety window BEFORE the watermark so a
        # late-ingested row (event_time <= watermark, ingested after the last pull) is
        # not lost to a strict `>`; a first pull reads from the retention floor.
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
        """Refine the table edges with ``system.access.column_lineage`` pairs (#901).

        Reads the same window as the table pull (same bound floor — no interpolation)
        and joins on the table pair built from the row's SPLIT catalog/schema/name
        columns through the same :func:`asset_identity.format_unity_catalog_name` the
        table edges used — **by construction byte-identical**, where the raw
        ``*_full_name`` string could diverge from the folded identity for a quoted
        mixed-case table (review finding). A column row whose table pair produced no
        table edge in this window is dropped (logged): a pair we can't anchor to an
        edge would fabricate lineage the table grain never saw. Pairs are capped per
        edge — a runaway wide-schema join must not balloon the edge row.
        """
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
