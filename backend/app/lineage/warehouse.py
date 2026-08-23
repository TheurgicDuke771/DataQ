"""Warehouse-native lineage — pull edges straight from the warehouse (#858, ADR 0034)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from backend.app.services.asset_identity import AssetIdentity


class LineageTier(StrEnum):
    """Which source answered a warehouse lineage pull — surfaced so a degraded graph
    never reads as a confident one (#828).
    """

    # Snowflake
    SNOWFLAKE_GET_LINEAGE = "snowflake_get_lineage"  # SNOWFLAKE.CORE.GET_LINEAGE (Enterprise+)
    SNOWFLAKE_ACCESS_HISTORY = "snowflake_access_history"  # ACCOUNT_USAGE.ACCESS_HISTORY (Ent+)
    SNOWFLAKE_OBJECT_DEPENDENCIES = "snowflake_object_dependencies"  # view-level, all editions
    # Unity Catalog
    UNITY_CATALOG_SYSTEM_ACCESS = "unity_catalog_system_access"  # system.access.table_lineage
    NONE = "none"

    @property
    def is_column_level(self) -> bool:
        """True for tiers that carry column-level detail (used to label the graph)."""
        return self in {self.SNOWFLAKE_GET_LINEAGE, self.SNOWFLAKE_ACCESS_HISTORY}


# Defensive per-edge cap on persisted column pairs (#901/#908): real schemas are bounded, but a
# generated/exploded join must not balloon the edge's JSONB.
MAX_COLUMN_PAIRS_PER_EDGE = 500


@dataclass(frozen=True)
class LineageEdgePair:
    """One directed edge as two OpenLineage identities — the warehouse provider's
    output unit. Both endpoints are already in the engine's own case, so they join
    `assets` byte-for-byte with no fold (the whole point vs the catalog seam).
    """

    upstream: AssetIdentity
    downstream: AssetIdentity
    column_pairs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class WarehouseLineageResult:
    """A successful warehouse pull: the edges found, the tier that produced them, and a
    human note when the answer is degraded (edition-gated, missing grant) — never a
    silent empty (#828).
    """

    edges: tuple[LineageEdgePair, ...]
    tier: LineageTier
    degraded_reason: str | None = None
    freshness_lag: str | None = None
    # Tiers whose absence was detected during the preflight/ladder descent — carried so
    # the UI/log can say "GET_LINEAGE unavailable (edition), fell back to …".
    skipped_tiers: tuple[str, ...] = field(default_factory=tuple)
    # The high-water mark of the source's event log.
    new_watermark: datetime | None = None
    # Is this pull a COMPLETE-ENOUGH observation of current state for a snapshot provider's refresh
    # to PRUNE against it (#1109 review)?
    prunable: bool = True

    @classmethod
    def empty(
        cls, tier: LineageTier = LineageTier.NONE, *, degraded_reason: str | None = None
    ) -> WarehouseLineageResult:
        """A pull that ran and found no edges — a true observation the refresh may
        prune on, unlike :class:`WarehouseLineageUnavailableError` (which it must not).
        """
        return cls(edges=(), tier=tier, degraded_reason=degraded_reason)


class WarehouseLineageUnavailableError(RuntimeError):
    """The warehouse could not be consulted at all (connect failure, missing grant on every tier,
    unreadable response). The refresh must leave the cache untouched — wiping edges on an outage
    is the failure mode this signal prevents (mirrors
    `lineage.provider.LineageUnavailableError`).
    """


@runtime_checkable
class WarehouseLineageProvider(Protocol):
    """Provider-agnostic warehouse lineage pull — one SQL round of tiers per connection."""

    source: str
    is_incremental: bool

    def enumerate_tables(
        self,
        conn: object,
        *,
        connection_config: dict[str, object],
        limit: int | None = None,
    ) -> tuple[AssetIdentity, ...]:
        """Enumerate the tables this connection can see, as OpenLineage identities (ADR 0040 — the
        table-enumeration seam). Reads the engine's own catalog views in the engine's own case —
        the #823-safe path, so an enumerated table joins `assets` byte-for-byte with what a
        suite target or lineage edge produces.
        """
        ...

    def fetch_edges(
        self,
        conn: object,
        *,
        connection_config: dict[str, object],
        since: datetime | None = None,
    ) -> WarehouseLineageResult:
        """Pull lineage edges over an already-open SQLAlchemy ``conn`` (the caller owns its
        lifecycle, via `profile_service._open_connection`).
        """
        ...


def get_warehouse_lineage_provider(connection_type: str) -> WarehouseLineageProvider | None:
    """The warehouse-native `WarehouseLineageProvider` for a datasource type, or ``None``
    for a type with no warehouse-native lineage (flat-file/Iceberg — those get lineage
    from dbt/OpenLineage, not a warehouse system view). Lazy-imports the impls so this
    module stays free of the SQLAlchemy-heavy provider modules until a caller needs one.
    """
    if connection_type == "snowflake":
        from backend.app.lineage.warehouse_snowflake import SnowflakeLineageProvider

        return SnowflakeLineageProvider()
    if connection_type == "unity_catalog":
        from backend.app.lineage.warehouse_unity_catalog import UnityCatalogLineageProvider

        return UnityCatalogLineageProvider()
    return None


def dedupe_edges(edges: Sequence[LineageEdgePair]) -> tuple[LineageEdgePair, ...]:
    """Collapse duplicate ``(upstream, downstream)`` pairs, preserving first-seen order."""
    seen: set[tuple[tuple[str, str], tuple[str, str]]] = set()
    out: list[LineageEdgePair] = []
    for edge in edges:
        up = (edge.upstream.namespace, edge.upstream.name)
        down = (edge.downstream.namespace, edge.downstream.name)
        if up == down:
            continue  # self-edge — not lineage
        key = (up, down)
        if key in seen:
            continue
        seen.add(key)
        out.append(edge)
    return tuple(out)
