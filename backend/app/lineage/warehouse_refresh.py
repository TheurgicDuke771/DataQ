"""Persist warehouse-native lineage edges into the `lineage_edges` cache (#858).

The write side of `lineage.warehouse`, modeled on `lineage.edges.refresh_dbt_edges`
(the connection-scoped regime) — warehouse pulls carry a REAL ``connection_id`` and
key on the full ``(upstream, downstream, source, connection_id)`` constraint, so a
Snowflake refresh never touches a Unity-Catalog or dbt row.

Simpler than the dbt refresh in one way: a warehouse provider returns
:class:`AssetIdentity` pairs already in the engine's own case, each carrying its own
namespace — so there is **no anchor-namespace heuristic** and **no fold** (the whole
point of warehouse-native lineage, `lineage.warehouse` docstring). Materialize every
endpoint as an asset, upsert the edges, prune the connection's stale edges by
``last_seen`` — the exact `clock_timestamp()` discipline the dbt path uses.

**Fail-open + never-prune-on-unavailable.** A provider that could not consult the
warehouse raises :class:`WarehouseLineageUnavailableError`; the refresh returns ``None``
and leaves the cache **untouched** — wiping edges on an outage is the failure this
guard prevents (#828). A *successful* pull with zero edges is a true observation and
DOES prune.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.app.core.logging import get_logger
from backend.app.db.models import Connection, LineageEdge
from backend.app.lineage.warehouse import (
    LineageTier,
    WarehouseLineageProvider,
    WarehouseLineageResult,
    WarehouseLineageUnavailableError,
)
from backend.app.services.asset_service import upsert_assets

log = get_logger(__name__)

_EDGE_CHUNK = 500


@dataclass(frozen=True)
class WarehouseRefreshOutcome:
    """The result of one warehouse-lineage refresh — the live edge count plus the tier
    that answered and its degrade note, so the caller (beat task, connection-health) can
    record WHICH tier the graph came from (#828) without re-reading the provider."""

    live_edges: int
    tier: LineageTier
    degraded_reason: str | None
    freshness_lag: str | None


def refresh_warehouse_edges(
    session: Session,
    *,
    connection: Connection,
    provider: WarehouseLineageProvider,
    conn: object,
) -> WarehouseRefreshOutcome | None:
    """Refresh ``connection``'s warehouse-native `lineage_edges` from ``provider``.

    ``conn`` is an already-open SQLAlchemy connection to the datasource (the caller
    owns it — `profile_service._open_connection`). Never raises. Returns the outcome,
    or ``None`` when skipped fail-soft (warehouse unavailable → cache untouched, or any
    error).
    """
    try:
        result = provider.fetch_edges(conn, connection_config=dict(connection.config))
    except WarehouseLineageUnavailableError as exc:
        # Learned nothing → do NOT prune. Leave the cache as-is for the next clean pass.
        log.warning(
            "warehouse_lineage_unavailable",
            connection_id=str(connection.id),
            source=provider.source,
            reason=str(exc),
        )
        return None
    except Exception as exc:  # any other provider error is fail-soft too
        log.warning(
            "warehouse_lineage_fetch_failed",
            connection_id=str(connection.id),
            source=provider.source,
            error_type=type(exc).__name__,
        )
        return None

    try:
        return _persist(session, connection=connection, provider=provider, result=result)
    except Exception as exc:  # a DB hiccup must never break the caller
        log.warning(
            "warehouse_lineage_persist_failed",
            connection_id=str(connection.id),
            source=provider.source,
            error=str(exc),
        )
        session.rollback()
        return None


def _persist(
    session: Session,
    *,
    connection: Connection,
    provider: WarehouseLineageProvider,
    result: WarehouseLineageResult,
) -> WarehouseRefreshOutcome:
    source = provider.source
    # clock_timestamp() advances within the tx (unlike now()), captured BEFORE the edge
    # upserts stamp a strictly-later last_seen — the prune's strict `<` then keeps every
    # just-seen edge and drops only edges from an earlier refresh (the dbt discipline,
    # correct even when two refreshes share one transaction in the test harness).
    refresh_started_at = session.execute(select(func.clock_timestamp())).scalar_one()

    identities = {
        (ident.namespace, ident.name)
        for edge in result.edges
        for ident in (edge.upstream, edge.downstream)
    }
    if identities:
        asset_rows = [
            {"namespace": ns, "name": nm, "env": connection.env, "connection_id": connection.id}
            for (ns, nm) in sorted(identities)
        ]
        # preserve_provenance: a warehouse pull must not flip a suite-resolved asset's
        # env/connection to this one.
        id_by_name = upsert_assets(session, asset_rows, preserve_provenance=True)
        edge_rows = _edge_rows(result, id_by_name, source=source, connection_id=connection.id)
        _upsert_edges(session, edge_rows)

    # Prune this (source, connection) scope. A successful empty pull prunes to zero; the
    # unavailable case never reaches here (returned None above), so a prune is always
    # backed by evidence we DID read the warehouse.
    session.execute(
        delete(LineageEdge).where(
            LineageEdge.source == source,
            LineageEdge.connection_id == connection.id,
            LineageEdge.last_seen < refresh_started_at,
        )
    )
    live = session.execute(
        select(func.count())
        .select_from(LineageEdge)
        .where(LineageEdge.source == source, LineageEdge.connection_id == connection.id)
    ).scalar_one()
    session.commit()
    log.info(
        "warehouse_lineage_refreshed",
        connection_id=str(connection.id),
        source=source,
        tier=str(result.tier),
        edges=int(live),
        degraded=result.degraded_reason is not None,
        skipped_tiers=list(result.skipped_tiers),
    )
    return WarehouseRefreshOutcome(
        live_edges=int(live),
        tier=result.tier,
        degraded_reason=result.degraded_reason,
        freshness_lag=result.freshness_lag,
    )


def _edge_rows(
    result: WarehouseLineageResult,
    id_by_name: dict[tuple[str, str], uuid.UUID],
    *,
    source: str,
    connection_id: uuid.UUID,
) -> list[dict[str, Any]]:
    seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
    rows: list[dict[str, Any]] = []
    for edge in result.edges:
        up = id_by_name[(edge.upstream.namespace, edge.upstream.name)]
        down = id_by_name[(edge.downstream.namespace, edge.downstream.name)]
        if (up, down) in seen:
            continue
        seen.add((up, down))
        rows.append(
            {
                "upstream_asset_id": up,
                "downstream_asset_id": down,
                "source": source,
                "connection_id": connection_id,
                "last_seen": func.clock_timestamp(),
            }
        )
    return rows


def _upsert_edges(
    session: Session, edge_rows: list[dict[str, Any]], *, chunk_size: int = _EDGE_CHUNK
) -> None:
    for start in range(0, len(edge_rows), chunk_size):
        chunk = edge_rows[start : start + chunk_size]
        stmt = pg_insert(LineageEdge).values(chunk)
        session.execute(
            stmt.on_conflict_do_update(
                constraint="uq_lineage_edges_up_down_source_conn",
                set_={"last_seen": func.clock_timestamp()},
            )
        )
