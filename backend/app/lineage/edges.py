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
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.app.core.logging import get_logger
from backend.app.db.models import Asset, Connection, LineageEdge
from backend.app.lineage.dbt_manifest import ManifestGraph, NodeIdentity
from backend.app.services.asset_identity import format_snowflake_name, format_unity_catalog_name
from backend.app.services.asset_service import upsert_assets

log = get_logger(__name__)

# dbt adapters whose identifiers fold like Unity Catalog (lower-case unquoted) —
# reuse asset_identity's UC rules so a databricks-adapter dbt node name matches a
# suite-resolved UC asset byte-for-byte.
_UC_ADAPTERS = frozenset({"databricks", "spark"})

# Warn when fewer than this fraction of manifest nodes matched an existing asset —
# a low match rate signals a probable mis-anchor (wrong namespace borrowed).
_LOW_ANCHOR_MATCH_RATIO = 0.30

# Multi-row INSERT chunk size for the edge upserts (mirrors the asset batch).
_EDGE_CHUNK = 500


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
    # One pass over the graph nodes → the canonical OL name per uid + the distinct
    # name set (the anchor lookup keys + the asset rows to materialize).
    canonical: dict[str, str] = {}
    names: set[str] = set()
    for uid, ident in graph.nodes.items():
        name = _canonical_name(graph.adapter_type, ident)
        if name:
            canonical[uid] = name
            names.add(name)
    if not names:
        log.warning("dbt_lineage_empty_graph", connection_id=str(connection.id))
        return None

    namespace = _resolve_namespace(session, connection=connection, names=sorted(names))
    if namespace is None:
        log.warning(
            "dbt_lineage_no_namespace_anchor",
            connection_id=str(connection.id),
            fix_hint="create a suite on one of the dbt project's tables to seed an asset "
            "namespace, or set `lineage_namespace` on the dbt connection config",
        )
        return None

    # `clock_timestamp()` (wall clock, advances *within* a transaction) — NOT
    # `now()` (== transaction start, constant for the whole tx). Captured before the
    # edge upserts (which stamp a strictly-later clock_timestamp on `last_seen`), so
    # the prune's strict `<` keeps every just-seen edge and drops only edges last
    # touched in an earlier refresh — correct even when two refreshes share one
    # transaction (the test harness's savepoint mode) where `now()` would be equal.
    refresh_started_at = session.execute(select(func.clock_timestamp())).scalar_one()

    # Batch-materialize every node as an asset under the anchor namespace,
    # preserving any datasource-resolved provenance (env / connection_id) already on
    # the row — a dbt refresh must not flip a suite-resolved asset to the dbt conn.
    asset_rows = [
        {
            "namespace": namespace,
            "name": name,
            "env": connection.env,
            "connection_id": connection.id,
        }
        for name in sorted(names)
    ]
    id_by_name = upsert_assets(session, asset_rows, preserve_provenance=True)
    asset_ids = {uid: id_by_name[(namespace, name)] for uid, name in canonical.items()}

    edge_rows = _edge_rows(graph, asset_ids, connection_id=connection.id)
    _upsert_edges(session, edge_rows)
    _prune_stale(session, connection_id=connection.id, refresh_started_at=refresh_started_at)
    live = session.execute(
        select(func.count())
        .select_from(LineageEdge)
        .where(LineageEdge.source == "dbt", LineageEdge.connection_id == connection.id)
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

    Shares the suite-target resolver's exact folding so a dbt-derived asset name
    matches a suite-derived one byte-for-byte: Snowflake → `format_snowflake_name`
    (upper unquoted), databricks/spark → `format_unity_catalog_name` (lower unquoted,
    matching UC assets). Any other adapter joins ``database.schema.name`` verbatim —
    a v1 posture (the OL case rules for that engine land when a real connection of
    that adapter does).
    """
    if adapter_type == "snowflake":
        return format_snowflake_name(ident.database, ident.schema, ident.name)
    if adapter_type in _UC_ADAPTERS:
        return format_unity_catalog_name(ident.database, ident.schema, ident.name)
    return ".".join(part for part in (ident.database, ident.schema, ident.name) if part)


def _resolve_namespace(session: Session, *, connection: Connection, names: list[str]) -> str | None:
    """The OL namespace to file this manifest's assets under.

    An operator-pinned ``lineage_namespace`` on the dbt connection config bypasses
    the heuristic entirely (used verbatim). Otherwise it is inferred from existing
    assets — see :func:`_anchor_namespace`.
    """
    pinned = connection.config.get("lineage_namespace")
    if isinstance(pinned, str) and pinned.strip():
        return pinned.strip()
    return _anchor_namespace(session, names=names, env=connection.env)


def _anchor_namespace(session: Session, *, names: list[str], env: str | None) -> str | None:
    """The OL namespace to file this manifest's assets under, inferred from assets.

    dbt's manifest has no namespace (no account/host), so we borrow it from assets
    DataQ already resolved (via suite targets) for the same table names.

    **Env-strict, no cross-env fallback**: the candidate pool is assets whose ``env``
    matches the connection (or is unknown / NULL). A QA project is never anchored into
    the PROD namespace just because no QA asset exists yet — no match → ``None``
    (caller skips fail-soft with an operator hint). The namespace is chosen by
    **majority**, then deterministically: most-recent ``last_seen``, then the
    lexicographically-smallest namespace (so equal-timestamp ties never flip-flop
    between refreshes). A low node→asset match rate is warned (mis-anchor signal).
    """
    rows = session.execute(
        select(Asset.namespace, Asset.env, Asset.name, Asset.last_seen).where(Asset.name.in_(names))
    ).all()
    pool = [r for r in rows if r.env == env or r.env is None]
    if not pool:
        return None
    matched = len({r.name for r in pool})
    if matched < len(names) * _LOW_ANCHOR_MATCH_RATIO:
        log.warning("dbt_lineage_low_anchor_match", matched=matched, total=len(names), env=env)
    counts: Counter[str] = Counter(str(r.namespace) for r in pool)
    top_count = max(counts.values())
    top = sorted(ns for ns, c in counts.items() if c == top_count)
    if len(top) == 1:
        return top[0]
    log.warning("dbt_lineage_namespace_anchor_tie", namespaces=top)
    # Deterministic tie-break: latest last_seen per namespace, then lexicographic.
    latest_by_ns = {ns: max(r.last_seen for r in pool if str(r.namespace) == ns) for ns in top}
    best_ts = max(latest_by_ns.values())
    return sorted(ns for ns, ts in latest_by_ns.items() if ts == best_ts)[0]


def _edge_rows(
    graph: ManifestGraph, asset_ids: dict[str, uuid.UUID], *, connection_id: uuid.UUID
) -> list[dict[str, Any]]:
    """De-duplicated `lineage_edges` insert rows for the graph's resolvable edges.

    `clock_timestamp()` on `last_seen` so every observed edge gets a strictly-later
    stamp than the refresh's captured start — the basis of the staleness prune.
    """
    seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
    rows: list[dict[str, Any]] = []
    for parent_uid, child_uid in graph.edges:
        upstream = asset_ids.get(parent_uid)
        downstream = asset_ids.get(child_uid)
        if upstream is None or downstream is None or (upstream, downstream) in seen:
            continue
        seen.add((upstream, downstream))
        rows.append(
            {
                "upstream_asset_id": upstream,
                "downstream_asset_id": downstream,
                "source": "dbt",
                "connection_id": connection_id,
                "last_seen": func.clock_timestamp(),
            }
        )
    return rows


def _upsert_edges(
    session: Session, edge_rows: list[dict[str, Any]], *, chunk_size: int = _EDGE_CHUNK
) -> None:
    """Chunked multi-row edge upsert (bump `last_seen` on an already-seen edge)."""
    for start in range(0, len(edge_rows), chunk_size):
        chunk = edge_rows[start : start + chunk_size]
        stmt = pg_insert(LineageEdge).values(chunk)
        session.execute(
            stmt.on_conflict_do_update(
                constraint="uq_lineage_edges_up_down_source_conn",
                set_={"last_seen": func.clock_timestamp()},
            )
        )


def _prune_stale(
    session: Session, *, connection_id: uuid.UUID, refresh_started_at: datetime
) -> None:
    """Delete this connection's dbt edges not re-seen in the latest refresh.

    Scoped by ``(source='dbt', connection_id)`` so a refresh of one project (or any
    other lineage source) never prunes another's edges — provenance, not an
    endpoint-set heuristic (the review's cross-project-corruption fix).
    """
    session.execute(
        delete(LineageEdge).where(
            LineageEdge.source == "dbt",
            LineageEdge.connection_id == connection_id,
            LineageEdge.last_seen < refresh_started_at,
        )
    )


def downstream_assets(session: Session, asset_id: uuid.UUID, *, max_depth: int = 10) -> list[Asset]:
    """Distinct downstream assets of ``asset_id`` in BFS order (blast radius)."""
    return [asset for asset, _depth in _walk(session, asset_id, "down", max_depth)]


def upstream_assets(session: Session, asset_id: uuid.UUID, *, max_depth: int = 10) -> list[Asset]:
    """Distinct upstream assets of ``asset_id`` in BFS order (provenance)."""
    return [asset for asset, _depth in _walk(session, asset_id, "up", max_depth)]


@dataclass(frozen=True)
class LineageNeighbourhood:
    """The lineage subgraph around one asset — enough to *draw* it (#805).

    The flat `upstream_assets` / `downstream_assets` lists answer "what is reachable",
    which is all the blast-radius consumers need. A graph view additionally needs to
    know **how far** each node sits (to lay it out in hop columns) and **which node
    connects to which** (to draw a truthful edge rather than a guessed one) — so this
    carries the hop distance per node and the edges actually traversed.

    ``edges`` are normalized ``(upstream_id, downstream_id)`` pairs regardless of the
    direction they were discovered in, so the two walks compose into one DAG.
    """

    upstream: list[tuple[Asset, int]]
    downstream: list[tuple[Asset, int]]
    edges: list[tuple[uuid.UUID, uuid.UUID]]


def lineage_neighbourhood(
    session: Session, asset_id: uuid.UUID, *, max_depth: int = 10
) -> LineageNeighbourhood:
    """Both walks from ``asset_id``, with hop depth per node + the traversed edges."""
    up, up_edges = _walk_graph(session, asset_id, "up", max_depth)
    down, down_edges = _walk_graph(session, asset_id, "down", max_depth)

    ids = [aid for aid, _ in up] + [aid for aid, _ in down]
    by_id = (
        {a.id: a for a in session.scalars(select(Asset).where(Asset.id.in_(ids)))} if ids else {}
    )
    return LineageNeighbourhood(
        upstream=[(by_id[aid], d) for aid, d in up if aid in by_id],
        downstream=[(by_id[aid], d) for aid, d in down if aid in by_id],
        edges=sorted(set(up_edges) | set(down_edges)),
    )


def _walk(
    session: Session, start: uuid.UUID, direction: str, max_depth: int
) -> list[tuple[Asset, int]]:
    """`_walk_graph`, resolved to `Asset` rows (dropping the edges)."""
    order, _edges = _walk_graph(session, start, direction, max_depth)
    if not order:
        return []
    ids = [aid for aid, _ in order]
    by_id = {a.id: a for a in session.scalars(select(Asset).where(Asset.id.in_(ids)))}
    return [(by_id[aid], depth) for aid, depth in order if aid in by_id]


def _walk_graph(
    session: Session, start: uuid.UUID, direction: str, max_depth: int
) -> tuple[list[tuple[uuid.UUID, int]], set[tuple[uuid.UUID, uuid.UUID]]]:
    """Depth-capped BFS over `lineage_edges` in ``direction`` from ``start``.

    Source-agnostic (blast radius spans every lineage source, not just dbt).
    De-duplicates and caps at ``max_depth`` hops, returning the distinct reachable
    asset ids **with their hop distance** in discovery (BFS) order, plus every edge
    traversed — including cross-edges back into already-visited nodes, so the
    subgraph is faithful and not just a spanning tree.

    Edges come back normalized as ``(upstream_id, downstream_id)`` whichever way we
    walked, so an up-walk and a down-walk can be unioned into one DAG.
    """
    if direction == "down":
        from_col, to_col = LineageEdge.upstream_asset_id, LineageEdge.downstream_asset_id
    else:
        from_col, to_col = LineageEdge.downstream_asset_id, LineageEdge.upstream_asset_id

    visited = {start}
    frontier = [start]
    order: list[tuple[uuid.UUID, int]] = []
    edges: set[tuple[uuid.UUID, uuid.UUID]] = set()
    depth = 0
    while frontier and depth < max_depth:
        rows = session.execute(select(from_col, to_col).where(from_col.in_(frontier))).all()
        next_frontier: list[uuid.UUID] = []
        for src, dst in rows:
            # `src`/`dst` are in walk order; store the edge in its true direction.
            edges.add((src, dst) if direction == "down" else (dst, src))
            if dst not in visited:
                visited.add(dst)
                order.append((dst, depth + 1))
                next_frontier.append(dst)
        frontier = next_frontier
        depth += 1
    return order, edges
