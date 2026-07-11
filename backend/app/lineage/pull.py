"""Pull catalog lineage into the `lineage_edges` cache (ADR 0034, #762).

The write side of the `LineageProvider` seam: :func:`get_lineage_provider` builds the
configured provider (dark by default), and :func:`refresh_pulled_edges` seeds a pull
from DataQ's known assets, collapses the returned graph to dataset→dataset edges, and
upserts them into `lineage_edges` with ``source='marquez'``.

**Coexistence with dbt edges (the #762 AC — merge without duplication).** Pulled edges
are provenance-tagged ``source='marquez'`` and carry a **NULL** ``connection_id`` — a
catalog pull has no orchestration connection, unlike a dbt refresh. They are keyed by
the ``(upstream, downstream, source) WHERE connection_id IS NULL`` **partial** unique
index (migration ``1a2b3c4d5e6f``), so a Marquez refresh dedupes within itself and its
prune is scoped to ``(source='marquez', connection_id IS NULL)`` — it can *never* touch
a ``source='dbt'`` row (those key on the full ``(…, connection_id)`` constraint). The
same physical ``(A→B)`` pair can therefore exist as both a dbt row and a Marquez row:
distinct sources, distinct rows, no merge — and the source-agnostic blast-radius walk
(`lineage.edges.downstream_assets`) traverses both.

**Fail-open** (mirrors `lineage.edges.refresh_dbt_edges`): a dead provider, a garbage
payload, or a DB hiccup logs a warning and returns ``None`` — pull is a browse/reason
convenience, never a liveness path.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger
from backend.app.db.models import Asset, LineageEdge
from backend.app.lineage.marquez import MarquezLineageProvider
from backend.app.lineage.provider import (
    LineageGraph,
    LineageNodeKind,
    LineageProvider,
    LineageUnavailableError,
)
from backend.app.services.asset_service import upsert_assets

log = get_logger(__name__)

# The lineage source tag stamped on every pulled edge (the prune scope).
_SOURCE = "marquez"

# How many hops out from each seed asset to pull. A cache of external truth, not a live
# path — a few hops around each monitored dataset is the blast-radius neighbourhood.
_PULL_DEPTH = 3

# Multi-row INSERT chunk size for the edge upserts (mirrors `lineage.edges`).
_EDGE_CHUNK = 500


def get_lineage_provider() -> LineageProvider | None:
    """The configured `LineageProvider`, or ``None`` when unconfigured (dark by default).

    Reads typed ``Settings`` (``lineage_provider`` + ``marquez_url``) — the emitter's
    gate pattern — so a value in ``.env.app`` (which the process env never sees)
    activates the pull. ``lineage_provider`` unset → ``None`` (no pull). An unknown
    provider name, or ``marquez`` without a URL, logs a warning and returns ``None``.
    """
    settings = get_settings()
    name = (settings.lineage_provider or "").strip().lower()
    if not name:
        return None
    if name == "marquez":
        if not settings.marquez_url:
            log.warning("lineage_provider_marquez_no_url")
            return None
        return MarquezLineageProvider(settings.marquez_url)
    log.warning("lineage_provider_unknown", provider=name)
    return None


def refresh_pulled_edges(
    session: Session, *, provider: LineageProvider, depth: int = _PULL_DEPTH
) -> int | None:
    """Refresh the pulled `lineage_edges` cache from ``provider``; return the live count.

    Never raises. Returns the number of live ``source='marquez'`` edges after the
    refresh, or ``None`` when skipped fail-soft (no seed assets, or any error).
    """
    try:
        return _refresh_pulled_edges(session, provider=provider, depth=depth)
    except Exception as exc:  # fail-open: pull must never break anything
        log.warning("lineage_pull_refresh_failed", provider=provider.provider, error=str(exc))
        session.rollback()
        return None


def _refresh_pulled_edges(session: Session, *, provider: LineageProvider, depth: int) -> int | None:
    # Seed from every asset DataQ already knows (the datasets it monitors) — Marquez's
    # lineage API is node-anchored, so a pull needs seeds. Discovered upstream/
    # downstream datasets are materialized as assets too (blast radius spans tables
    # DataQ doesn't monitor).
    seeds = session.execute(select(Asset.namespace, Asset.name)).all()
    if not seeds:
        log.info("lineage_pull_no_seed_assets")
        return None

    name_pairs, unavailable = _collect_dataset_edges(provider, seeds, depth=depth)
    if unavailable:
        # The catalog couldn't be (fully) consulted — we learned nothing about the
        # missing seeds, so DO NOT prune: wiping the cache on an outage is the failure
        # mode this branch exists to prevent. Upsert whatever WAS fetched (still
        # fresher than nothing) and leave the rest untouched until a clean refresh.
        log.warning(
            "lineage_pull_partial_unavailable",
            provider=provider.provider,
            seeds=len(seeds),
            unavailable=unavailable,
            fetched_pairs=len(name_pairs),
        )
        if not name_pairs:
            return None
    if not name_pairs and not unavailable:
        log.info("lineage_pull_no_edges", provider=provider.provider, seeds=len(seeds))
        # Genuinely-empty observation → previously cached edges are now stale.
        refresh_started_at = _clock(session)
        _prune_stale(session, refresh_started_at=refresh_started_at)
        session.commit()
        return 0

    # `clock_timestamp()` (advances within the tx) captured before the edge upserts
    # (which stamp a strictly-later `last_seen`), so the prune's strict `<` keeps every
    # just-seen edge and drops only edges last touched in an earlier refresh — the exact
    # discipline `lineage.edges` uses.
    refresh_started_at = _clock(session)

    # Materialize every endpoint dataset as an asset (NULL provenance — a pull has no
    # connection; `preserve_provenance` keeps a datasource-resolved asset's env/conn).
    identities = {ident for pair in name_pairs for ident in pair}
    asset_rows = [
        {"namespace": ns, "name": nm, "env": None, "connection_id": None}
        for (ns, nm) in sorted(identities)
    ]
    id_by_name = upsert_assets(session, asset_rows, preserve_provenance=True)

    edge_rows = _edge_rows(name_pairs, id_by_name)
    _upsert_edges(session, edge_rows)
    if not unavailable:
        # Prune only on a CLEAN refresh — with any seed unavailable, an absent edge is
        # indistinguishable from an unconsulted one, so stale rows wait for the next
        # clean pass instead of being wiped by an outage.
        _prune_stale(session, refresh_started_at=refresh_started_at)
    live = session.execute(
        select(func.count())
        .select_from(LineageEdge)
        .where(LineageEdge.source == _SOURCE, LineageEdge.connection_id.is_(None))
    ).scalar_one()
    session.commit()
    log.info(
        "lineage_pull_refreshed",
        provider=provider.provider,
        seeds=len(seeds),
        edges=int(live),
        unavailable_seeds=unavailable,
    )
    return int(live)


def _clock(session: Session) -> datetime:
    return cast(datetime, session.execute(select(func.clock_timestamp())).scalar_one())


def _collect_dataset_edges(
    provider: LineageProvider, seeds: Sequence[Any], *, depth: int
) -> tuple[set[tuple[tuple[str, str], tuple[str, str]]], int]:
    """Pull each seed's graph, merge, and collapse to dataset→dataset OL-name pairs.

    Returns ``(pairs, unavailable)``: a deduped set of
    ``((up_ns, up_name), (down_ns, down_name))`` plus the count of seeds whose pull
    raised :class:`LineageUnavailableError` — the caller's no-prune-on-outage signal.
    Job (and any non-dataset) nodes are collapsed through, so only dataset endpoints —
    the only kind with an `assets` identity today — reach the cache.
    """
    nodes: dict[str, Any] = {}
    edges: set[tuple[str, str]] = set()
    unavailable = 0
    for namespace, name in seeds:
        try:
            graph = provider.get_lineage(namespace=namespace, name=name, depth=depth)
        except LineageUnavailableError:
            unavailable += 1
            continue
        for node_id, node in graph.nodes.items():
            nodes[node_id] = node
        edges.update(graph.edges)
    return _collapse_to_datasets(LineageGraph(nodes=nodes, edges=tuple(edges))), unavailable


def _collapse_to_datasets(
    graph: LineageGraph,
) -> set[tuple[tuple[str, str], tuple[str, str]]]:
    """Contract non-dataset nodes to dataset→dataset edges (single non-dataset hop).

    Marquez lineage is bipartite dataset↔job: ``dataset_A → job_J → dataset_B``. For
    each non-dataset node we join its upstream datasets to its downstream datasets; a
    direct ``dataset_A → dataset_B`` edge passes through unchanged. Only nodes with a
    resolved ``(namespace, name)`` identity participate — an identity-less dataset node
    is dropped (not crashed).
    """
    datasets: dict[str, tuple[str, str]] = {
        node_id: (node.namespace, node.name)
        for node_id, node in graph.nodes.items()
        if node.kind is LineageNodeKind.DATASET and node.namespace and node.name
    }
    out_adj: dict[str, set[str]] = defaultdict(set)
    in_adj: dict[str, set[str]] = defaultdict(set)
    for up, down in graph.edges:
        out_adj[up].add(down)
        in_adj[down].add(up)

    pairs: set[tuple[tuple[str, str], tuple[str, str]]] = set()
    for node_id, node in graph.nodes.items():
        if node_id in datasets:
            for down in out_adj.get(node_id, ()):
                if down in datasets:
                    pairs.add((datasets[node_id], datasets[down]))
            continue
        if node.kind is LineageNodeKind.DATASET:
            # An identity-less DATASET node is dropped, NOT bridged through — treating
            # it as a hop would synthesize a direct edge that skips a real dataset.
            continue
        # Non-dataset node (job / unknown): join its dataset upstreams to downstreams.
        ups = [s for s in in_adj.get(node_id, ()) if s in datasets]
        downs = [d for d in out_adj.get(node_id, ()) if d in datasets]
        for a in ups:
            for b in downs:
                if a != b:
                    pairs.add((datasets[a], datasets[b]))
    return pairs


def _edge_rows(
    name_pairs: set[tuple[tuple[str, str], tuple[str, str]]],
    id_by_name: dict[tuple[str, str], uuid.UUID],
) -> list[dict[str, Any]]:
    """`lineage_edges` insert rows for the collapsed pairs (NULL connection, marquez).

    Self-edges (an asset resolving to itself after collapse) are dropped. `last_seen`
    uses `clock_timestamp()` so every observed edge stamps strictly later than the
    captured refresh start — the basis of the staleness prune.
    """
    rows: list[dict[str, Any]] = []
    for up_name, down_name in name_pairs:
        up = id_by_name.get(up_name)
        down = id_by_name.get(down_name)
        if up is None or down is None or up == down:
            continue
        rows.append(
            {
                "upstream_asset_id": up,
                "downstream_asset_id": down,
                "source": _SOURCE,
                "connection_id": None,
                "last_seen": func.clock_timestamp(),
            }
        )
    return rows


def _upsert_edges(
    session: Session, edge_rows: list[dict[str, Any]], *, chunk_size: int = _EDGE_CHUNK
) -> None:
    """Chunked multi-row upsert onto the NULL-connection partial unique index.

    Targets ``(upstream, downstream, source) WHERE connection_id IS NULL`` (the
    partial index from migration ``1a2b3c4d5e6f``) — the dedup key for connection-less
    sources; on an already-seen edge it just bumps ``last_seen``.
    """
    for start in range(0, len(edge_rows), chunk_size):
        chunk = edge_rows[start : start + chunk_size]
        stmt = pg_insert(LineageEdge).values(chunk)
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["upstream_asset_id", "downstream_asset_id", "source"],
                index_where=LineageEdge.connection_id.is_(None),
                set_={"last_seen": func.clock_timestamp()},
            )
        )


def _prune_stale(session: Session, *, refresh_started_at: datetime) -> None:
    """Delete pulled edges not re-seen in the latest refresh.

    Scoped by ``(source='marquez', connection_id IS NULL)`` so it can never touch a
    ``source='dbt'`` edge (which always carries a non-NULL connection_id) — provenance,
    not a heuristic (the same cross-source-safety `lineage.edges._prune_stale` gives dbt).
    """
    session.execute(
        delete(LineageEdge).where(
            LineageEdge.source == _SOURCE,
            LineageEdge.connection_id.is_(None),
            LineageEdge.last_seen < refresh_started_at,
        )
    )
