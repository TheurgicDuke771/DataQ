"""Cache dbt-manifest lineage into `lineage_edges`, and walk it (ADR 0034, #759).

`refresh_dbt_edges` is the write side: it canonicalizes a parsed
:class:`~backend.app.lineage.dbt_manifest.ManifestGraph`'s nodes into OpenLineage
asset names, materializes an `assets` row per node, upserts one `lineage_edges`
row per edge (``source='dbt'``), and prunes edges the latest refresh no longer
observed (``last_seen`` staleness cutoff) — a **refreshed cache of external
truth**, not a graph DataQ authors.

`downstream_assets` / `upstream_assets` are the read side: a depth-capped BFS over
`lineage_edges` — the blast-radius query the incident evidence card and the asset
page consume.

**Fail-open is the contract.** Lineage is a browse/reason convenience layered over
the execution model; a bad manifest, a missing namespace anchor, or a DB hiccup
must never break run ingestion or suite triggering. `refresh_dbt_edges` therefore
never raises — it logs a structlog warning and returns ``None``. Precedent:
`lineage.dispatch` / `alerting.builder`.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.app.core.logging import get_logger
from backend.app.db.models import Asset, Connection, LineageEdge
from backend.app.lineage.dbt_manifest import ManifestGraph, NodeIdentity
from backend.app.services.asset_identity import format_snowflake_name
from backend.app.services.asset_service import upsert_asset

log = get_logger(__name__)


def refresh_dbt_edges(
    session: Session, *, connection: Connection, graph: ManifestGraph
) -> int | None:
    """Refresh the dbt `lineage_edges` cache from ``graph``; return the live count.

    Never raises. Returns the number of live ``source='dbt'`` edges among this
    manifest's assets after the refresh, or ``None`` when the refresh is skipped
    fail-soft (no namespace anchor, empty graph, or any error).
    """
    try:
        return _refresh_dbt_edges(session, connection=connection, graph=graph)
    except Exception as exc:  # fail-open: lineage must never break the run path
        log.warning(
            "dbt_lineage_refresh_failed",
            connection_id=str(connection.id),
            error=str(exc),
        )
        session.rollback()
        return None


def _refresh_dbt_edges(
    session: Session, *, connection: Connection, graph: ManifestGraph
) -> int | None:
    canonical = {
        uid: _canonical_name(graph.adapter_type, ident) for uid, ident in graph.nodes.items()
    }
    names = [name for name in canonical.values() if name]
    if not names:
        log.warning("dbt_lineage_empty_graph", connection_id=str(connection.id))
        return None

    namespace = _anchor_namespace(session, names=names, env=connection.env)
    if namespace is None:
        log.warning(
            "dbt_lineage_no_namespace_anchor",
            connection_id=str(connection.id),
            fix_hint="create a suite on one of the dbt project's tables to seed an asset namespace",
        )
        return None

    # `clock_timestamp()` (wall clock, advances *within* a transaction) — NOT
    # `now()` (== transaction start, constant for the whole tx). Captured before the
    # edge upserts (which stamp a strictly-later clock_timestamp on `last_seen`), so
    # the prune's strict `<` keeps every just-seen edge and drops only edges last
    # touched in an earlier refresh — correct even when two refreshes share one
    # transaction (the test harness's savepoint mode) where `now()` would be equal.
    refresh_started_at = session.execute(select(func.clock_timestamp())).scalar_one()

    asset_ids: dict[str, uuid.UUID] = {}
    for uid, name in canonical.items():
        if not name:
            continue
        asset_ids[uid] = upsert_asset(
            session,
            namespace=namespace,
            name=name,
            env=connection.env,
            connection_id=connection.id,
        )

    for parent_uid, child_uid in graph.edges:
        upstream = asset_ids.get(parent_uid)
        downstream = asset_ids.get(child_uid)
        if upstream is None or downstream is None:
            continue
        _upsert_edge(session, upstream=upstream, downstream=downstream)

    ids = list(asset_ids.values())
    _prune_stale(session, asset_ids=ids, refresh_started_at=refresh_started_at)
    live = session.execute(
        select(func.count())
        .select_from(LineageEdge)
        .where(
            LineageEdge.source == "dbt",
            LineageEdge.upstream_asset_id.in_(ids),
            LineageEdge.downstream_asset_id.in_(ids),
        )
    ).scalar_one()
    session.commit()
    log.info(
        "dbt_lineage_refreshed",
        connection_id=str(connection.id),
        namespace=namespace,
        nodes=len(asset_ids),
        edges=int(live),
    )
    return int(live)


def _canonical_name(adapter_type: str, ident: NodeIdentity) -> str:
    """Canonicalize a node identity to its OpenLineage ``name`` string.

    Snowflake reuses the suite-target resolver's exact folding
    (`format_snowflake_name`) so a dbt-derived asset name matches a suite-derived
    one byte-for-byte. Other adapters join ``database.schema.name`` verbatim — a
    v1 posture (the OL case rules for those engines land when a real connection of
    that adapter does), documented here.
    """
    if adapter_type == "snowflake":
        return format_snowflake_name(ident.database, ident.schema, ident.name)
    return ".".join(part for part in (ident.database, ident.schema, ident.name) if part)


def _anchor_namespace(session: Session, *, names: list[str], env: str | None) -> str | None:
    """The OL namespace to file this manifest's assets under, from existing assets.

    dbt's manifest has no namespace (no account/host), so we borrow it from assets
    DataQ already resolved (via suite targets) for the same table names. Prefer
    rows whose ``env`` matches the connection; take the namespace by majority, ties
    broken by most-recent ``last_seen`` (logged). No matching asset at all →
    ``None`` (caller skips fail-soft).
    """
    rows = session.execute(
        select(Asset.namespace, Asset.env, Asset.last_seen).where(Asset.name.in_(names))
    ).all()
    if not rows:
        return None
    env_rows = [r for r in rows if r.env == env]
    pool = env_rows or rows
    counts: Counter[str] = Counter(str(r.namespace) for r in pool)
    top_count = max(counts.values())
    top = [ns for ns, c in counts.items() if c == top_count]
    if len(top) == 1:
        return top[0]
    log.warning("dbt_lineage_namespace_anchor_tie", namespaces=sorted(top))
    winner = max((r for r in pool if r.namespace in top), key=lambda r: r.last_seen)
    return str(winner.namespace)


def _upsert_edge(session: Session, *, upstream: uuid.UUID, downstream: uuid.UUID) -> None:
    # `clock_timestamp()` on BOTH insert and conflict-update so every observed edge
    # gets a strictly-later `last_seen` than the refresh's captured start — the
    # basis of the staleness prune (see `refresh_started_at`).
    now_expr = func.clock_timestamp()
    stmt = (
        pg_insert(LineageEdge)
        .values(
            upstream_asset_id=upstream,
            downstream_asset_id=downstream,
            source="dbt",
            last_seen=now_expr,
        )
        .on_conflict_do_update(
            constraint="uq_lineage_edges_up_down_source",
            set_={"last_seen": now_expr},
        )
    )
    session.execute(stmt)


def _prune_stale(
    session: Session, *, asset_ids: list[uuid.UUID], refresh_started_at: datetime
) -> None:
    """Delete dbt edges among this manifest's assets not re-seen this refresh.

    Scoped to edges whose *both* endpoints are in ``asset_ids`` so a refresh of one
    project never prunes another project's edges that happen to be stale.
    """
    session.execute(
        delete(LineageEdge).where(
            LineageEdge.source == "dbt",
            LineageEdge.last_seen < refresh_started_at,
            LineageEdge.upstream_asset_id.in_(asset_ids),
            LineageEdge.downstream_asset_id.in_(asset_ids),
        )
    )


def downstream_assets(session: Session, asset_id: uuid.UUID, *, max_depth: int = 10) -> list[Asset]:
    """Distinct downstream assets of ``asset_id`` in BFS order (blast radius)."""
    return _walk(session, asset_id, direction="down", max_depth=max_depth)


def upstream_assets(session: Session, asset_id: uuid.UUID, *, max_depth: int = 10) -> list[Asset]:
    """Distinct upstream assets of ``asset_id`` in BFS order (provenance)."""
    return _walk(session, asset_id, direction="up", max_depth=max_depth)


def _walk(session: Session, start: uuid.UUID, *, direction: str, max_depth: int) -> list[Asset]:
    """Depth-capped BFS over `lineage_edges` in ``direction`` from ``start``.

    Source-agnostic (blast radius spans every lineage source, not just dbt).
    De-duplicates, caps at ``max_depth`` hops, and returns the distinct reachable
    assets in discovery (BFS) order.
    """
    if direction == "down":
        from_col, to_col = LineageEdge.upstream_asset_id, LineageEdge.downstream_asset_id
    else:
        from_col, to_col = LineageEdge.downstream_asset_id, LineageEdge.upstream_asset_id

    visited = {start}
    frontier = [start]
    order: list[uuid.UUID] = []
    depth = 0
    while frontier and depth < max_depth:
        reached = session.execute(select(to_col).where(from_col.in_(frontier))).scalars().all()
        next_frontier: list[uuid.UUID] = []
        for aid in reached:
            if aid not in visited:
                visited.add(aid)
                order.append(aid)
                next_frontier.append(aid)
        frontier = next_frontier
        depth += 1

    if not order:
        return []
    by_id = {a.id: a for a in session.scalars(select(Asset).where(Asset.id.in_(order)))}
    return [by_id[aid] for aid in order if aid in by_id]
