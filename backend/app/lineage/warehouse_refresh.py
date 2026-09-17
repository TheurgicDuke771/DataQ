"""Persist warehouse-native lineage edges into the `lineage_edges` cache (#858).

A snapshot pull that only PARTIALLY observed current state is persisted accrete-only,
with no stale-edge prune, so a transient blip cannot wipe real edges. That trade used to
promise "the next clean pull prunes normally, so a genuinely removed dependency is at
worst one cycle late" — a promise nothing enforced (#1236): a persistent condition that
reads as transient suspends pruning on every cycle, forever.

Two things now enforce and report it:

* **A bounded backstop.** ``connections.lineage_last_authoritative_refresh_at`` records
  when the cache was last pruned. Once that is older than ``LINEAGE_STALE_AFTER_HOURS``,
  the next successful pull prunes anyway and logs ``warehouse_lineage_prune_forced`` at
  WARNING — a stale-but-honest graph beats an unboundedly accreting one. It re-stamps,
  so the suspension resumes for another window rather than degrading to "always prune on
  a partial pull".
* **A reported suspension.** :class:`WarehouseRefreshOutcome` carries the suspension and
  its age through to the connection and ``warehouse_lineage_status``, so the lineage
  banner can say pruning has been suspended since X instead of it living only in a log.

A NULL stamp means no prune has ever been recorded for the connection, and the backstop
deliberately does NOT fire on it: a first-ever partial pull would delete edges accreted
from earlier partial pulls, against an observation never shown to be complete. That state
is reported instead.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.db.models import Connection, LineageEdge
from backend.app.lineage.warehouse import (
    MAX_COLUMN_PAIRS_PER_EDGE,
    LineageTier,
    WarehouseLineageProvider,
    WarehouseLineageResult,
    WarehouseLineageUnavailableError,
    get_warehouse_lineage_provider,
)
from backend.app.services import credential_health
from backend.app.services.asset_service import upsert_assets
from backend.app.services.failure_classifier import classify_failure_reason

log = get_logger(__name__)

_EDGE_CHUNK = 500


@dataclass(frozen=True)
class WarehouseRefreshOutcome:
    """The result of one warehouse-lineage refresh — the live edge count plus the tier
    that answered and its degrade note, so the caller (beat task, connection-health) can
    record WHICH tier the graph came from (#828) without re-reading the provider.
    """

    live_edges: int
    tier: LineageTier
    degraded_reason: str | None
    freshness_lag: str | None
    # For an incremental (log) source, the high-water mark the caller persists and
    # passes back as ``since`` next refresh. ``None`` for a snapshot source.
    new_watermark: datetime | None = None
    # #1236 — whether THIS pull pruned, and if not, how long pruning has been suspended.
    # Always False for an incremental source, which never prunes by design.
    prune_suspended: bool = False
    # When the cache was last pruned. ``None`` means never — an age that cannot be stated,
    # which is a different answer from "recently" and must not render as one.
    prune_suspended_since: datetime | None = None
    # The backstop fired: a partial pull pruned anyway because the suspension outlived
    # ``LINEAGE_STALE_AFTER_HOURS``.
    prune_forced: bool = False


def refresh_warehouse_edges(
    session: Session,
    *,
    connection: Connection,
    provider: WarehouseLineageProvider,
    conn: object,
    since: datetime | None = None,
) -> WarehouseRefreshOutcome | None:
    """Refresh ``connection``'s warehouse-native `lineage_edges` from ``provider``."""
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
    # The snapshot regime's two destructive halves — the stale-edge prune and the verbatim `columns`
    # replace — are BOTH claims that this pull is the current truth.
    authoritative_snapshot = not provider.is_incremental and result.prunable
    # The backstop (#1236): a partial snapshot pull prunes anyway once the suspension has
    # outlived the staleness window. `columns` still merges rather than replacing — only the
    # stale-edge half is forced, since a partial pull's column set genuinely is partial.
    prune_forced = (
        not provider.is_incremental
        and not result.prunable
        and _suspension_exhausted(connection.lineage_last_authoritative_refresh_at)
    )
    prune = authoritative_snapshot or prune_forced
    # clock_timestamp() advances within the tx (unlike now()), captured BEFORE the edge upserts
    # stamp a strictly-later last_seen.
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
        # Column-pair regime follows the EDGE regime (#911 review): an incremental (log) source
        # unions pairs with the persisted prior.
        existing_columns = (
            {}
            if authoritative_snapshot
            else _existing_columns(session, source=source, connection_id=connection.id)
        )
        edge_rows = _edge_rows(
            result,
            id_by_name,
            source=source,
            connection_id=connection.id,
            existing_columns=existing_columns,
        )
        _upsert_edges(session, edge_rows, replace_columns=authoritative_snapshot)

    # Prune ONLY a snapshot source, and only when the pull observed current state completely
    # enough (Snowflake OBJECT_DEPENDENCIES — a current-state view) or the backstop fired.
    if prune:
        session.execute(
            delete(LineageEdge).where(
                LineageEdge.source == source,
                LineageEdge.connection_id == connection.id,
                LineageEdge.last_seen < refresh_started_at,
            )
        )
        if prune_forced:
            log.warning(
                "warehouse_lineage_prune_forced",
                connection_id=str(connection.id),
                source=source,
                tier=str(result.tier),
                reason=result.degraded_reason,
                suspended_since=_isoformat(connection.lineage_last_authoritative_refresh_at),
                threshold_hours=get_settings().lineage_stale_after_hours,
            )
        # Re-stamped on a forced prune too: the suspension then resumes for another window
        # instead of the backstop degrading into "prune on every partial pull".
        connection.lineage_last_authoritative_refresh_at = refresh_started_at
    elif not provider.is_incremental:
        # A snapshot source that skipped its prune is a WARNING, not a detail (#1109 review).
        log.warning(
            "warehouse_lineage_prune_suspended",
            connection_id=str(connection.id),
            source=source,
            tier=str(result.tier),
            reason=result.degraded_reason,
            suspended_since=_isoformat(connection.lineage_last_authoritative_refresh_at),
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
        # A snapshot refresh that did NOT prune is the interesting one to see in the logs — it means
        # the pull was partial.
        pruned=prune,
        prune_forced=prune_forced,
    )
    return WarehouseRefreshOutcome(
        live_edges=int(live),
        tier=result.tier,
        degraded_reason=result.degraded_reason,
        freshness_lag=result.freshness_lag,
        new_watermark=result.new_watermark,
        prune_suspended=not prune and not provider.is_incremental,
        prune_suspended_since=connection.lineage_last_authoritative_refresh_at,
        prune_forced=prune_forced,
    )


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _suspension_exhausted(last_pruned_at: datetime | None) -> bool:
    """Has pruning been suspended longer than ``LINEAGE_STALE_AFTER_HOURS``?

    ``None`` is False, not True: no prune has ever been recorded, so there is no
    suspension age to exceed, and forcing one would delete edges accreted from earlier
    partial pulls against an observation never shown to be complete. A non-positive
    threshold disables the backstop entirely, matching how the same setting already
    disables the staleness signal.
    """
    threshold_hours = get_settings().lineage_stale_after_hours
    if last_pruned_at is None or threshold_hours <= 0:
        return False
    return datetime.now(UTC) - last_pruned_at > timedelta(hours=threshold_hours)


def refresh_connection_lineage(
    session: Session, *, connection: Connection, secret_store: SecretStore
) -> WarehouseRefreshOutcome | None:
    """Refresh one warehouse connection's lineage AND persist its refresh state (#858)."""
    provider = get_warehouse_lineage_provider(connection.type)
    if provider is None:
        return None

    # Lazy import: profile_service pulls the heavy datasource stack; keep it off this
    # module's import cost (and clear of any import cycle) until a refresh actually runs.
    from backend.app.services.profile_service import _open_connection

    # A snapshot source ignores the stored watermark; a log source reads from it.
    since = connection.lineage_watermark if provider.is_incremental else None
    # Credential-health seam (#1697). This path SWALLOWS its exception into connection
    # state, so the rejection is handed over explicitly — a clean return would otherwise
    # read as a working credential.
    with credential_health.credential_use(session, connection) as credential:
        try:
            with _open_connection(connection, secret_store) as conn:
                outcome = refresh_warehouse_edges(
                    session, connection=connection, provider=provider, conn=conn, since=since
                )
        except Exception as exc:
            # Opening the datasource failed (bad/unreadable credential, unreachable host).
            credential.failed(exc)
            _record_refresh_error(session, connection, exc)
            return None

    if outcome is None:
        # refresh_warehouse_edges already logged the unavailable/failed cause and left
        # the cache untouched; surface it as connection state so the UI/health can see it.
        _record_refresh_error(session, connection, RuntimeError("warehouse lineage unavailable"))
        return None

    connection.lineage_last_refresh_at = datetime.now(UTC)
    connection.lineage_last_tier = str(outcome.tier)
    # Bounded write: the reason is a joined list of constructed per-tier notes (#902), and the
    # column is String(512) — overflow must degrade to a clipped note.
    reason = outcome.degraded_reason
    connection.lineage_degraded_reason = reason[:512] if reason else None
    # Deliberately cleared even on a TRANSIENTLY degraded pull (#1109 review considered setting it):
    # `lineage_last_error` means "the last refresh could not run".
    connection.lineage_last_error = None
    if outcome.new_watermark is not None:
        connection.lineage_watermark = outcome.new_watermark
    session.commit()
    return outcome


def _record_refresh_error(session: Session, connection: Connection, exc: Exception) -> None:
    """Stamp a classified refresh error onto the connection (never raw text)."""
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
    regime forbids).
    """
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
                # Exclude JSON 'null' in SQL (#907): rows bulk-written before `none_as_null` (or by
                # an old image in the deploy window) carry it, pass `is_not(None)`.
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
        # Column pairs accrete (union with what the edge already carries): a pair is forgotten only
        # when its whole edge is pruned.
        merged: list[list[str]] | None = None
        prior = existing_columns.get((up, down))
        if edge.column_pairs or prior:
            union = {tuple(p) for p in (prior or [])} | set(edge.column_pairs)
            merged = [list(p) for p in sorted(union)[:MAX_COLUMN_PAIRS_PER_EDGE]]
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
    """Upsert the refresh's edge rows, with per-regime `columns` semantics (#911):"""
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
