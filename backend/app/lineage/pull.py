"""Pull catalog lineage into the `lineage_edges` cache (ADR 0034, #762)."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, cast

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger
from backend.app.db.models import Asset, LineageEdge
from backend.app.lineage.identity import canonical_identity
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
    """The configured `LineageProvider`, or ``None`` when unconfigured (dark by default)."""
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


def lineage_provider_unset() -> bool:
    """Whether the pull is UNSET — as opposed to configured-but-unusable (#1090)."""
    return not (get_settings().lineage_provider or "").strip()


def purge_orphaned_pulled_edges(session: Session) -> int:
    """Delete every pulled edge once the provider is de-configured (#1090 disposition)."""
    try:
        purged = session.execute(
            delete(LineageEdge).where(
                LineageEdge.source == _SOURCE, LineageEdge.connection_id.is_(None)
            )
        ).rowcount  # type: ignore[attr-defined]  # DELETE always yields a CursorResult
        session.commit()
    except Exception:
        session.rollback()
        log.warning("lineage_pull_orphan_purge_failed", source=_SOURCE, exc_info=True)
        return 0
    if purged:
        log.warning("lineage_pull_orphans_purged", source=_SOURCE, edges=int(purged))
    return int(purged or 0)


def refresh_pulled_edges(
    session: Session, *, provider: LineageProvider, depth: int = _PULL_DEPTH
) -> int | None:
    """Refresh the pulled `lineage_edges` cache from ``provider``; return the live count."""
    try:
        return _refresh_pulled_edges(session, provider=provider, depth=depth)
    except Exception as exc:  # fail-open: pull must never break anything
        log.warning("lineage_pull_refresh_failed", provider=provider.provider, error=str(exc))
        session.rollback()
        return None


def _refresh_pulled_edges(session: Session, *, provider: LineageProvider, depth: int) -> int | None:
    # Seed from every asset DataQ already knows (the datasets it monitors) — Marquez's lineage API
    # is node-anchored, so a pull needs seeds.
    seeds = session.execute(select(Asset.namespace, Asset.name)).all()
    if not seeds:
        log.info("lineage_pull_no_seed_assets")
        return None

    name_pairs, outcome = _collect_dataset_edges(provider, seeds, depth=depth)
    unavailable = outcome.unavailable

    if outcome.resolved == 0 and not unavailable and outcome.absent:
        # The catalog answered, and knows NONE of our assets.
        log.warning(
            "lineage_pull_no_seed_matched_catalog",
            provider=provider.provider,
            seeds=len(seeds),
            absent=outcome.absent,
            hint="no asset matched any catalog dataset — check namespace/name alignment",
        )

    if unavailable:
        # The catalog couldn't be (fully) consulted — we learned nothing about the missing seeds.
        log.warning(
            "lineage_pull_partial_unavailable",
            provider=provider.provider,
            seeds=len(seeds),
            unavailable=unavailable,
            absent=outcome.absent,
            ambiguous=outcome.ambiguous,
            resolved=outcome.resolved,
            fetched_pairs=len(name_pairs),
        )
        if not name_pairs:
            return None
    if not name_pairs and not unavailable and outcome.resolved == 0:
        # The catalog answered and matched NONE of our assets.
        return None

    if not name_pairs and not unavailable:
        log.info(
            "lineage_pull_no_edges",
            provider=provider.provider,
            seeds=len(seeds),
            resolved=outcome.resolved,
            absent=outcome.absent,
            ambiguous=outcome.ambiguous,
        )
        # Genuinely-empty observation → previously cached edges are now stale.
        refresh_started_at = _clock(session)
        _prune_stale(session, refresh_started_at=refresh_started_at)
        session.commit()
        return 0

    # `clock_timestamp()` (advances within the tx) captured before the edge upserts (which stamp a
    # strictly-later `last_seen`).
    refresh_started_at = _clock(session)

    # Resolve every catalog identity to a DataQ identity BEFORE it becomes an asset (#823).
    resolve = _identity_resolver(seeds)
    name_pairs = {(resolve(up), resolve(down)) for (up, down) in name_pairs}

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
        # indistinguishable from an unconsulted one.
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
        # The three outcomes stay distinct all the way to the log line.
        resolved_seeds=outcome.resolved,
        absent_seeds=outcome.absent,
        ambiguous_seeds=outcome.ambiguous,
        unavailable_seeds=unavailable,
    )
    return int(live)


def _clock(session: Session) -> datetime:
    return cast(datetime, session.execute(select(func.clock_timestamp())).scalar_one())


@dataclass(frozen=True)
class _SeedOutcome:
    """What the catalog had to say about our seeds — the three cases kept DISTINCT."""

    unavailable: int = 0
    """Seeds whose catalog call errored — we learned NOTHING (no prune)."""
    absent: int = 0
    """Assets the catalog holds no dataset for — a true observation, not a failure."""
    resolved: int = 0
    """Assets matched to a catalog dataset and pulled."""
    ambiguous: int = 0
    """Assets whose fold key matched >1 catalog dataset — refused, never guessed."""


def _catalog_index(names: Sequence[str], namespace: str) -> dict[tuple[str, str], list[str]]:
    """Index a namespace's catalog dataset names by canonical identity."""
    index: dict[tuple[str, str], list[str]] = defaultdict(list)
    for name in names:
        index[canonical_identity(namespace, name)].append(name)
    return index


def _identity_resolver(
    seeds: Sequence[Any],
) -> Callable[[tuple[str, str]], tuple[str, str]]:
    """Map a catalog identity onto the DataQ identity it belongs to."""
    by_exact: set[tuple[str, str]] = set()
    by_key: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for namespace, name in seeds:
        by_exact.add((namespace, name))
        by_key[canonical_identity(namespace, name)].append((namespace, name))

    def resolve(identity: tuple[str, str]) -> tuple[str, str]:
        if identity in by_exact:
            return identity
        key = canonical_identity(*identity)
        owners = by_key.get(key, [])
        if len(owners) == 1:
            return owners[0]
        if len(owners) > 1:
            log.warning(
                "lineage_pull_ambiguous_asset",
                namespace=identity[0],
                catalog_name=identity[1],
                candidates=sorted(n for _ns, n in owners),
            )
            return identity
        return key

    return resolve


def _collect_dataset_edges(
    provider: LineageProvider, seeds: Sequence[Any], *, depth: int
) -> tuple[set[tuple[tuple[str, str], tuple[str, str]]], _SeedOutcome]:
    """Pull each seed's graph, merge, and collapse to dataset→dataset OL-name pairs."""
    nodes: dict[str, Any] = {}
    edges: set[tuple[str, str]] = set()
    outcome = _SeedOutcome()

    # One listing per namespace, not per asset — a workspace has a handful of
    # datasources and potentially thousands of assets.
    by_namespace: dict[str, list[str]] = defaultdict(list)
    for namespace, name in seeds:
        by_namespace[namespace].append(name)

    for namespace, asset_names in by_namespace.items():
        try:
            catalog_names = provider.list_datasets(namespace=namespace)
        except LineageUnavailableError:
            # The whole namespace is unconsultable — every asset under it is `unavailable`, never
            # `absent`.
            outcome = replace(outcome, unavailable=outcome.unavailable + len(asset_names))
            continue

        folded = _catalog_index(catalog_names, namespace)
        pulled: set[str] = set()  # a name shared by two assets is fetched once

        for name in asset_names:
            candidates = folded.get(canonical_identity(namespace, name), [])
            if not candidates:
                outcome = replace(outcome, absent=outcome.absent + 1)
                continue

            failed = 0
            for seed_name in candidates:
                if seed_name in pulled:
                    continue
                pulled.add(seed_name)
                try:
                    graph = provider.get_lineage(namespace=namespace, name=seed_name, depth=depth)
                except LineageUnavailableError:
                    failed += 1
                    continue
                for node_id, node in graph.nodes.items():
                    nodes[node_id] = node
                edges.update(graph.edges)

            if failed and failed == len(candidates):
                # Every name for this asset errored — we learned nothing about it.
                outcome = replace(outcome, unavailable=outcome.unavailable + 1)
            else:
                outcome = replace(outcome, resolved=outcome.resolved + 1)

    return _collapse_to_datasets(LineageGraph(nodes=nodes, edges=tuple(edges))), outcome


def _collapse_to_datasets(
    graph: LineageGraph,
) -> set[tuple[tuple[str, str], tuple[str, str]]]:
    """Contract non-dataset nodes to dataset→dataset edges (single non-dataset hop)."""
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
    """`lineage_edges` insert rows for the collapsed pairs (NULL connection, marquez)."""
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
    """Chunked multi-row upsert onto the NULL-connection partial unique index."""
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
    """Delete pulled edges not re-seen in the latest refresh."""
    session.execute(
        delete(LineageEdge).where(
            LineageEdge.source == _SOURCE,
            LineageEdge.connection_id.is_(None),
            LineageEdge.last_seen < refresh_started_at,
        )
    )
