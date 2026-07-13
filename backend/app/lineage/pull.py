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

    name_pairs, outcome = _collect_dataset_edges(provider, seeds, depth=depth)
    unavailable = outcome.unavailable

    if outcome.resolved == 0 and not unavailable and outcome.absent:
        # The catalog answered, and knows NONE of our assets. That is a legitimate
        # observation — but it is also exactly what a systematic identity mismatch looks
        # like (#823: every seed 404s because the producer spelled the name in another
        # case). Say so loudly: "we asked about N tables and the catalog had never heard
        # of any of them" is a configuration smell, not a normal steady state, and the
        # silent version of this is what kept the pull dark.
        log.warning(
            "lineage_pull_no_seed_matched_catalog",
            provider=provider.provider,
            seeds=len(seeds),
            absent=outcome.absent,
            hint="no asset matched any catalog dataset — check namespace/name alignment",
        )

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
            absent=outcome.absent,
            ambiguous=outcome.ambiguous,
            resolved=outcome.resolved,
            fetched_pairs=len(name_pairs),
        )
        if not name_pairs:
            return None
    if not name_pairs and not unavailable and outcome.resolved == 0:
        # The catalog answered and matched NONE of our assets. That is NOT a licence to
        # prune. Reclassifying a 404 seed from `unavailable` to `absent` (which is the
        # honest reading — the catalog is up, it simply has no such dataset) would
        # otherwise hand a systematic identity mismatch the power to DELETE every cached
        # edge: exactly the #823 failure, now with data loss on top. A prune is only
        # ever justified by evidence we can read the catalog *and* find our tables in
        # it — i.e. by `resolved > 0`.
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

    # `clock_timestamp()` (advances within the tx) captured before the edge upserts
    # (which stamp a strictly-later `last_seen`), so the prune's strict `<` keeps every
    # just-seen edge and drops only edges last touched in an earlier refresh — the exact
    # discipline `lineage.edges` uses.
    refresh_started_at = _clock(session)

    # Canonicalize every catalog identity BEFORE it becomes an asset (#823). The catalog
    # holds whatever case its producer emitted, and materializing that verbatim would
    # fork the asset: `DB.RETAIL.customers` from dbt would land alongside the
    # `DB.RETAIL.CUSTOMERS` a suite target already created — two assets, one table,
    # inside our own DB. Folding on the way in means a pulled dataset lands on the asset
    # the engine's own case would have produced, whoever emitted it.
    name_pairs = {(canonical_identity(*up), canonical_identity(*down)) for (up, down) in name_pairs}

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
        # The three outcomes stay distinct all the way to the log line — an operator
        # must be able to tell "the catalog is down" from "the catalog doesn't know my
        # tables" from "my tables have no lineage" without reading the code (#823/#828).
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
    """What the catalog had to say about our seeds — the three cases kept DISTINCT.

    Collapsing these is the bug #823/#828 are both about: "the catalog is unreachable",
    "the catalog has never heard of this table", and "the catalog knows the table and it
    genuinely has no lineage" are three different facts, and only the last one licenses
    a prune. Reported as three counters so a permanently-dark pull is visible in the
    logs instead of looking like an empty catalog.
    """

    unavailable: int = 0
    """Seeds whose catalog call errored — we learned NOTHING (no prune)."""
    absent: int = 0
    """Assets the catalog holds no dataset for — a true observation, not a failure."""
    resolved: int = 0
    """Assets matched to a catalog dataset and pulled."""
    ambiguous: int = 0
    """Assets whose fold key matched >1 catalog dataset — refused, never guessed."""


def _catalog_index(names: Sequence[str], namespace: str) -> dict[tuple[str, str], list[str]]:
    """Index a namespace's catalog dataset names by canonical identity.

    A list (not a single name) per key on purpose: two catalog datasets CAN fold to the
    same key (Snowflake's quoted `"orders"` and unquoted `ORDERS` are different tables),
    and the caller must refuse to guess rather than pick one.
    """
    index: dict[tuple[str, str], list[str]] = defaultdict(list)
    for name in names:
        index[canonical_identity(namespace, name)].append(name)
    return index


def _collect_dataset_edges(
    provider: LineageProvider, seeds: Sequence[Any], *, depth: int
) -> tuple[set[tuple[tuple[str, str], tuple[str, str]]], _SeedOutcome]:
    """Pull each seed's graph, merge, and collapse to dataset→dataset OL-name pairs.

    **Seeds are resolved against the catalog's own dataset names, not ours** (#823). A
    catalog byte-matches the node id it is handed, and a real producer emits whatever
    case its source spelled — `openlineage-dbt` emits `DB.SCHEMA.mart_orders` where our
    asset identity is `DB.SCHEMA.MART_ORDERS`. Seeding with our string 404s against a
    perfectly-populated catalog, so we enumerate what the catalog HAS
    (`provider.list_datasets`) and seed with its exact string, matched to our assets
    through `canonical_identity`.

    Exact match wins; the canonical fold is only a fallback; an ambiguous fold is
    refused. That ordering matters — the fold is deliberately lossy for the case-
    insensitive engines, so it must never override a name the catalog literally has.

    Returns ``(pairs, outcome)``.
    """
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
            # The whole namespace is unconsultable — every asset under it is
            # `unavailable`, never `absent`. Conflating the two would let an outage
            # look like "the catalog knows nothing", and prune the cache.
            outcome = replace(outcome, unavailable=outcome.unavailable + len(asset_names))
            continue

        exact = set(catalog_names)
        folded = _catalog_index(catalog_names, namespace)

        for name in asset_names:
            if name in exact:
                seed_name = name
            else:
                candidates = folded.get(canonical_identity(namespace, name), [])
                if len(candidates) > 1:
                    # Two catalog datasets fold to this asset's key. Picking one would
                    # be a coin-flip that draws a WRONG lineage edge; say so and skip.
                    log.warning(
                        "lineage_pull_ambiguous_dataset",
                        provider=provider.provider,
                        namespace=namespace,
                        asset=name,
                        candidates=sorted(candidates),
                    )
                    outcome = replace(outcome, ambiguous=outcome.ambiguous + 1)
                    continue
                if not candidates:
                    outcome = replace(outcome, absent=outcome.absent + 1)
                    continue
                seed_name = candidates[0]

            try:
                graph = provider.get_lineage(namespace=namespace, name=seed_name, depth=depth)
            except LineageUnavailableError:
                outcome = replace(outcome, unavailable=outcome.unavailable + 1)
                continue
            outcome = replace(outcome, resolved=outcome.resolved + 1)
            for node_id, node in graph.nodes.items():
                nodes[node_id] = node
            edges.update(graph.edges)

    return _collapse_to_datasets(LineageGraph(nodes=nodes, edges=tuple(edges))), outcome


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
