"""Warehouse-native lineage — pull edges straight from the warehouse (#858, ADR 0034).

The catalog `LineageProvider` (`lineage.provider`, Marquez) exists to reconcile a
byte-mismatched identity we *cannot construct* (#823 — a producer spells a name in
some other case, so we must enumerate the catalog and fold). **This seam solves a
different, simpler problem and is therefore a distinct interface.** Querying the
warehouse directly returns identifiers in the engine's OWN case — Snowflake
`ACCOUNT_USAGE` returns UPPER, Unity Catalog `system.access` returns lower — which is
**byte-identical to `services.asset_identity`**. No enumerate-and-fold step, no
node-graph normalization: a warehouse provider runs SQL and yields
``(upstream_identity, downstream_identity)`` edge pairs directly.

The pull is **incremental where the source is a log** (UC `table_lineage` has an
`event_time` watermark) and **snapshot-diff where the source is current-state**
(Snowflake `OBJECT_DEPENDENCIES` is a view, not a log — no event time, so its refresh
upserts + prunes by `last_seen` like the dbt path). Both write `lineage_edges` with a
real ``connection_id`` (the pull rides a datasource connection) — the non-NULL,
full-unique-constraint regime, so a Snowflake refresh never touches a UC or dbt row.

**Honest degradation (#828).** A warehouse offers lineage at tiers that vary by edition
and grant. The provider tries the richest available and reports **which tier answered**
so the UI can say a graph is view-level-only rather than paint a confident empty state.
The tier ladder and its live-verified behaviour are per-provider (see `snowflake.py`).
"""

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

    The values are stable UI/telemetry tags. ``NONE`` means the pull could run but no
    tier was available (e.g. every richer tier is edition-gated and the floor found
    nothing) — distinct from :class:`WarehouseLineageUnavailableError`, which means the pull
    could not run at all.
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


# Defensive per-edge cap on persisted column pairs (#901/#908): real schemas are
# bounded, but a generated/exploded join must not balloon the edge's JSONB. Shared by
# every provider that offers the column grain — a per-provider copy already drifted
# once (the SF port initially shipped uncapped; #911 review).
MAX_COLUMN_PAIRS_PER_EDGE = 500


@dataclass(frozen=True)
class LineageEdgePair:
    """One directed edge as two OpenLineage identities — the warehouse provider's
    output unit. Both endpoints are already in the engine's own case, so they join
    `assets` byte-for-byte with no fold (the whole point vs the catalog seam).

    ``column_pairs`` is the edge's column-grain refinement (#901) —
    ``(upstream_column, downstream_column)`` pairs from sources that offer it (UC
    ``system.access.column_lineage``). Empty for table-grain-only sources; the
    refresh persists it as the edge's ``columns`` JSONB.
    """

    upstream: AssetIdentity
    downstream: AssetIdentity
    column_pairs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class WarehouseLineageResult:
    """A successful warehouse pull: the edges found, the tier that produced them, and a
    human note when the answer is degraded (edition-gated, missing grant) — never a
    silent empty (#828).

    ``freshness_lag`` names the source's known staleness (Snowflake `ACCESS_HISTORY`
    lags 2-3h) so the UI can qualify "current as of ~3h ago"; ``None`` when the source
    is current-state (`OBJECT_DEPENDENCIES`, `GET_LINEAGE`).
    """

    edges: tuple[LineageEdgePair, ...]
    tier: LineageTier
    degraded_reason: str | None = None
    freshness_lag: str | None = None
    # Tiers whose absence was detected during the preflight/ladder descent — carried so
    # the UI/log can say "GET_LINEAGE unavailable (edition), fell back to …".
    skipped_tiers: tuple[str, ...] = field(default_factory=tuple)
    # The high-water mark of the source's event log, for an INCREMENTAL provider (UC's
    # `table_lineage` is append-only with an `event_time`): the caller persists it and
    # passes it back as ``since`` next refresh, so the pull re-reads only new events.
    # ``None`` for a SNAPSHOT provider (Snowflake `OBJECT_DEPENDENCIES` — no event time,
    # re-read whole each pass). An incremental result covering no new events still
    # carries the prior watermark so it never regresses.
    new_watermark: datetime | None = None
    # Is this pull a COMPLETE-ENOUGH observation of current state for a snapshot
    # provider's refresh to PRUNE against it (#1109 review)?
    #
    # ``True`` (the default) for the two honest cases: every tier answered, or a tier
    # was skipped for a CONFIRMED, structural reason — an edition gate, a missing
    # grant, a tier that genuinely observed nothing. Absence of an edge in such a pull
    # is real evidence the edge is gone, so the snapshot regime may prune it.
    #
    # ``False`` when a tier was skipped — or only partially traversed — for a
    # TRANSIENT/indeterminate reason (a network blip on one call, a failed catalog
    # read). Then the missing edges are *unobserved*, not *absent*, and pruning them
    # would wipe a previously-cached richer graph on a blip: the same #828
    # never-prune-on-unavailable failure, one notch down from a total outage. The
    # refresh degrades that cycle to accrete-only (upsert, never forget) and the next
    # clean pull prunes normally.
    prunable: bool = True

    @classmethod
    def empty(
        cls, tier: LineageTier = LineageTier.NONE, *, degraded_reason: str | None = None
    ) -> WarehouseLineageResult:
        """A pull that ran and found no edges — a true observation the refresh may
        prune on, unlike :class:`WarehouseLineageUnavailableError` (which it must not)."""
        return cls(edges=(), tier=tier, degraded_reason=degraded_reason)


class WarehouseLineageUnavailableError(RuntimeError):
    """The warehouse could not be consulted at all (connect failure, missing grant on
    every tier, unreadable response). The refresh must leave the cache untouched —
    wiping edges on an outage is the failure mode this signal prevents (mirrors
    `lineage.provider.LineageUnavailableError`). The message is CLASSIFIED (never raw
    exception text — it can carry a DSN/credential); the caller stores it as-is."""


@runtime_checkable
class WarehouseLineageProvider(Protocol):
    """Provider-agnostic warehouse lineage pull — one SQL round of tiers per connection.

    ``source`` is the stable tag stamped on pulled `lineage_edges` (``'snowflake'`` /
    ``'unity_catalog'``) — the connection-scoped prune scope, so one warehouse's
    refresh never touches another source's rows.

    ``is_incremental`` splits the two refresh regimes: a SNAPSHOT source (Snowflake
    `OBJECT_DEPENDENCIES`, a current-state view) is re-read whole and its stale edges
    PRUNED; a LOG source (UC `table_lineage`, append-only with `event_time`) is read
    forward from a watermark and its edges are NEVER pruned — an edge absent from the
    latest window is a historical fact, not a removed dependency. The refresh reads this
    flag to choose (`warehouse_refresh`).
    """

    source: str
    is_incremental: bool

    def enumerate_tables(
        self,
        conn: object,
        *,
        connection_config: dict[str, object],
        limit: int | None = None,
    ) -> tuple[AssetIdentity, ...]:
        """Enumerate the tables this connection can see, as OpenLineage identities
        (ADR 0040 — the table-enumeration seam). Reads the engine's own catalog
        views in the engine's own case — the #823-safe path, so an enumerated
        table joins `assets` byte-for-byte with what a suite target or lineage
        edge produces. Deterministic catalog order (each impl's full identifier
        tuple); ``limit`` bounds the read so a caller can detect overflow with
        ``limit=cap+1`` — the caller owns the honesty of any truncation (no
        silent caps).

        Consumers: the #919 inventory sync and (since #892) the Snowflake
        GET_LINEAGE per-seed traversal; the #466 pickers are DESIGNED to ride it
        but not wired yet — one seam, so discovery can never fork.
        """
        ...

    def fetch_edges(
        self,
        conn: object,
        *,
        connection_config: dict[str, object],
        since: datetime | None = None,
    ) -> WarehouseLineageResult:
        """Pull lineage edges over an already-open SQLAlchemy ``conn`` (the caller owns
        its lifecycle, via `profile_service._open_connection`). ``connection_config`` is
        the datasource's non-secret config — the provider needs it to build OpenLineage
        identities (Snowflake account → namespace; UC workspace host → namespace) that
        match `asset_identity` byte-for-byte.

        ``since`` is the last persisted watermark for an INCREMENTAL provider — it reads
        only events strictly after it and returns the new high-water mark on the result.
        A SNAPSHOT provider ignores ``since`` (documented on the impl).

        Descends its tier ladder, returning the richest available as a
        :class:`WarehouseLineageResult`. Raises :class:`WarehouseLineageUnavailableError`
        only when NO tier could run — an empty-but-successful pull returns
        :meth:`WarehouseLineageResult.empty`, which the refresh may prune on.

        A ladder descent driven by a TRANSIENT failure (rather than a confirmed edition
        gate / missing grant) must set ``prunable=False`` on the result — see that
        field. Descending is always safer than aborting, but only if the degraded answer
        it produces is not then mistaken for current truth.
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
    """Collapse duplicate ``(upstream, downstream)`` pairs, preserving first-seen order.

    A warehouse view can list the same dependency more than once (e.g. one edge per
    referencing column), and the log tiers replay events — the edge cache keys on the
    pair, so dedupe before upsert to keep the write set minimal and the counts honest.
    Self-edges (a table depending on itself, which some views emit for in-place
    rebuilds) are dropped — they are not lineage and would pollute the blast-radius walk.
    """
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
