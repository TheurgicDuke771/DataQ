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
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.db.models import Connection, LineageEdge
from backend.app.lineage.warehouse import (
    LineageTier,
    WarehouseLineageProvider,
    WarehouseLineageResult,
    WarehouseLineageUnavailableError,
    get_warehouse_lineage_provider,
)
from backend.app.services.asset_service import upsert_assets
from backend.app.services.failure_classifier import classify_failure_reason

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
    # For an incremental (log) source, the high-water mark the caller persists and
    # passes back as ``since`` next refresh. ``None`` for a snapshot source.
    new_watermark: datetime | None = None


def refresh_warehouse_edges(
    session: Session,
    *,
    connection: Connection,
    provider: WarehouseLineageProvider,
    conn: object,
    since: datetime | None = None,
) -> WarehouseRefreshOutcome | None:
    """Refresh ``connection``'s warehouse-native `lineage_edges` from ``provider``.

    ``conn`` is an already-open SQLAlchemy connection to the datasource (the caller
    owns it — `profile_service._open_connection`). ``since`` is the last persisted
    watermark for an incremental provider (the caller stores
    `WarehouseRefreshOutcome.new_watermark` and passes it back). Never raises. Returns
    the outcome, or ``None`` when skipped fail-soft (warehouse unavailable → cache
    untouched, or any error).

    Two regimes, chosen by ``provider.is_incremental``:

    * **snapshot** (Snowflake OBJECT_DEPENDENCIES) — re-read whole, PRUNE stale edges.
    * **incremental / log** (UC table_lineage) — read forward from ``since``, upsert,
      and NEVER prune: an edge absent from the latest window is a historical fact, not
      a removed dependency. Pruning it would erase real lineage.
    """
    try:
        result = provider.fetch_edges(conn, connection_config=dict(connection.config), since=since)
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
        # Column-pair regime follows the EDGE regime (#911 review): an incremental
        # (log) source unions pairs with the persisted prior — its window only
        # re-observes pairs whose queries ran inside it. A snapshot source's pull IS
        # the current truth: pairs replace, so a mapping the warehouse no longer
        # reports (rewritten ETL, revoked column-level grant) goes away instead of
        # accreting forever.
        existing_columns = (
            _existing_columns(session, source=source, connection_id=connection.id)
            if provider.is_incremental
            else {}
        )
        edge_rows = _edge_rows(
            result,
            id_by_name,
            source=source,
            connection_id=connection.id,
            existing_columns=existing_columns,
        )
        _upsert_edges(session, edge_rows, replace_columns=not provider.is_incremental)

    # Prune ONLY a snapshot source (Snowflake OBJECT_DEPENDENCIES — a current-state
    # view). A log source (UC table_lineage) is incremental: an edge absent from this
    # window is a historical fact, not a removed dependency, so pruning it would erase
    # real lineage. A successful empty snapshot pull prunes to zero; the unavailable
    # case never reaches here (returned None above), so a prune is always backed by
    # evidence we DID read the warehouse.
    if not provider.is_incremental:
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
        incremental=provider.is_incremental,
        degraded=result.degraded_reason is not None,
        skipped_tiers=list(result.skipped_tiers),
    )
    return WarehouseRefreshOutcome(
        live_edges=int(live),
        tier=result.tier,
        degraded_reason=result.degraded_reason,
        freshness_lag=result.freshness_lag,
        new_watermark=result.new_watermark,
    )


def refresh_connection_lineage(
    session: Session, *, connection: Connection, secret_store: SecretStore
) -> WarehouseRefreshOutcome | None:
    """Refresh one warehouse connection's lineage AND persist its refresh state (#858).

    The beat task's per-connection unit: resolve the provider for the connection type,
    open a datasource connection, run :func:`refresh_warehouse_edges` from the stored
    watermark, and record the outcome onto the connection —
    ``lineage_watermark`` (advanced for a log source), ``lineage_last_tier`` /
    ``lineage_degraded_reason`` (so the UI can qualify the graph, #828),
    ``lineage_last_refresh_at``, and a CLASSIFIED ``lineage_last_error`` (never raw
    exception text — the `last_poll_error` precedent).

    Fail-soft and self-contained: a connection whose warehouse is unreachable records a
    classified error and returns ``None`` without touching the edge cache — one bad
    connection never aborts the sweep. Returns ``None`` for a non-warehouse type (no
    provider) with no state written.
    """
    provider = get_warehouse_lineage_provider(connection.type)
    if provider is None:
        return None

    # Lazy import: profile_service pulls the heavy datasource stack; keep it off this
    # module's import cost (and clear of any import cycle) until a refresh actually runs.
    from backend.app.services.profile_service import _open_connection

    # A snapshot source ignores the stored watermark; a log source reads from it.
    since = connection.lineage_watermark if provider.is_incremental else None
    try:
        with _open_connection(connection, secret_store) as conn:
            outcome = refresh_warehouse_edges(
                session, connection=connection, provider=provider, conn=conn, since=since
            )
    except Exception as exc:
        # Opening the datasource failed (bad/unreadable credential, unreachable host).
        _record_refresh_error(session, connection, exc)
        return None

    if outcome is None:
        # refresh_warehouse_edges already logged the unavailable/failed cause and left
        # the cache untouched; surface it as connection state so the UI/health can see it.
        _record_refresh_error(session, connection, RuntimeError("warehouse lineage unavailable"))
        return None

    connection.lineage_last_refresh_at = datetime.now(UTC)
    connection.lineage_last_tier = str(outcome.tier)
    # Bounded write: the reason is a joined list of constructed per-tier notes (#902),
    # and the column is String(512) — overflow must degrade to a clipped note, never a
    # raw StringDataRightTruncation (the #813 class).
    reason = outcome.degraded_reason
    connection.lineage_degraded_reason = reason[:512] if reason else None
    connection.lineage_last_error = None
    if outcome.new_watermark is not None:
        connection.lineage_watermark = outcome.new_watermark
    session.commit()
    return outcome


def _record_refresh_error(session: Session, connection: Connection, exc: Exception) -> None:
    """Stamp a classified refresh error onto the connection (never raw text). Every
    caller reaches here via a path that already left the session write-clean — the
    provider raised before any edge write, or `refresh_warehouse_edges` rolled back its
    own partial persist — so this only records the health signal (no rollback here,
    which would also discard the connection row this must update)."""
    connection.lineage_last_refresh_at = datetime.now(UTC)
    connection.lineage_last_error = classify_failure_reason(exc)
    session.commit()
    log.warning(
        "warehouse_lineage_connection_refresh_failed",
        connection_id=str(connection.id),
        reason=connection.lineage_last_error,
    )


def _existing_columns(
    session: Session, *, source: str, connection_id: uuid.UUID
) -> dict[tuple[uuid.UUID, uuid.UUID], list[list[str]]]:
    """The connection's already-persisted column pairs, keyed by edge (#901) — the
    merge base for an incremental pull, whose window only re-observes pairs whose
    queries ran inside it (forgetting the rest would be a prune the never-prune
    regime forbids)."""
    return {
        (up, down): cols
        for up, down, cols in session.execute(
            select(
                LineageEdge.upstream_asset_id,
                LineageEdge.downstream_asset_id,
                LineageEdge.columns,
            ).where(
                LineageEdge.source == source,
                LineageEdge.connection_id == connection_id,
                LineageEdge.columns.is_not(None),
                # Exclude JSON 'null' in SQL (#907): rows bulk-written before
                # `none_as_null` (or by an old image in the deploy window) carry it,
                # pass `is_not(None)`, and would land as None values in a dict typed
                # list-of-pairs. jsonb_typeof makes the filter mean what it says.
                func.jsonb_typeof(LineageEdge.columns) != "null",
            )
        )
    }


def _edge_rows(
    result: WarehouseLineageResult,
    id_by_name: dict[tuple[str, str], uuid.UUID],
    *,
    source: str,
    connection_id: uuid.UUID,
    existing_columns: dict[tuple[uuid.UUID, uuid.UUID], list[list[str]]] | None = None,
) -> list[dict[str, Any]]:
    existing_columns = existing_columns or {}
    seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
    rows: list[dict[str, Any]] = []
    for edge in result.edges:
        up = id_by_name[(edge.upstream.namespace, edge.upstream.name)]
        down = id_by_name[(edge.downstream.namespace, edge.downstream.name)]
        if (up, down) in seen:
            continue
        seen.add((up, down))
        # Column pairs accrete (union with what the edge already carries): a pair is
        # forgotten only when its whole edge is pruned. NULL (never observed) stays
        # NULL — it is not the same claim as "observed, zero pairs".
        merged: list[list[str]] | None = None
        prior = existing_columns.get((up, down))
        if edge.column_pairs or prior:
            union = {tuple(p) for p in (prior or [])} | set(edge.column_pairs)
            merged = [list(p) for p in sorted(union)]
        rows.append(
            {
                "upstream_asset_id": up,
                "downstream_asset_id": down,
                "source": source,
                "connection_id": connection_id,
                "last_seen": func.clock_timestamp(),
                "columns": merged,
            }
        )
    return rows


def _upsert_edges(
    session: Session,
    edge_rows: list[dict[str, Any]],
    *,
    chunk_size: int = _EDGE_CHUNK,
    replace_columns: bool = False,
) -> None:
    """Upsert the refresh's edge rows, with per-regime `columns` semantics (#911):

    - **incremental** (``replace_columns=False``): EXCLUDED carries the pre-merged
      union (`_edge_rows`), and COALESCE never regresses a value another writer
      landed to NULL — this row's NULL only means "nothing observed this window".
    - **snapshot** (``replace_columns=True``): the pull is the current truth —
      EXCLUDED overwrites verbatim, so a pair the warehouse no longer reports (or a
      whole grain lost with a revoked grant) is cleared instead of frozen.
    """
    for start in range(0, len(edge_rows), chunk_size):
        chunk = edge_rows[start : start + chunk_size]
        stmt = pg_insert(LineageEdge).values(chunk)
        columns_value = (
            stmt.excluded.columns
            if replace_columns
            else func.coalesce(stmt.excluded.columns, LineageEdge.columns)
        )
        session.execute(
            stmt.on_conflict_do_update(
                constraint="uq_lineage_edges_up_down_source_conn",
                set_={"last_seen": func.clock_timestamp(), "columns": columns_value},
            )
        )
