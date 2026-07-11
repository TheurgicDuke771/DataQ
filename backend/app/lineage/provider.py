"""The `LineageProvider` seam — pull a lineage graph from a catalog (ADR 0034, #762).

Mirrors `orchestration.base.OrchestrationProvider`: one provider-agnostic interface
that speaks normalized DTOs, so service code routes by the configured provider and
never branches on the concrete catalog. **Marquez is the reference implementation**
(`lineage.marquez`); the same seam is the extension point for catalogs deferred by
ADR 0034 — **do not build these here**:

- **DataHub** — same seam, deferred until a user brings one (8 GB-RAM Kafka/OpenSearch
  footprint disqualifies it from the reference compose stack; our #758 emitter already
  feeds its native OpenLineage receiver).
- **OpenMetadata** — must **not** integrate via its `openmetadata-ingestion` SDK
  (Collate source-available, non-compete — prohibited by ADR 0031); a REST-only
  integration would need its own ADR.
- **Microsoft Purview** — parked (Atlas API, preview-grade OpenLineage, Azure-only —
  contra the wind-down posture).

**The node-kind contract (the #762 comment).** A pulled graph's downstream nodes are
**not always tables**: a governance catalog that ingests Power BI lineage surfaces a
*report* sitting downstream of a monitored dataset, and DataHub/Purview surface *jobs*
and *dashboards*. So every node carries a :class:`LineageNodeKind`, not an implicit
"this is a dataset" assumption. Today only ``DATASET`` nodes have an OpenLineage
identity that maps to a DataQ `assets` row (and thus to a `lineage_edges` edge);
``JOB`` nodes are collapsed through, and every other kind (BI report, dashboard, an
unknown a newer catalog emits) **parses without crashing** and is carried on the graph
so a future capable provider round-trips it into the asset model with no seam change.

**Pure + read-only.** `get_lineage` performs one catalog read and returns a
:class:`LineageGraph`; it never writes. Persisting the pulled edges into the
`lineage_edges` cache is a separate concern (`lineage.pull.refresh_pulled_edges`), the
same split as `orchestration.base` (parse) vs `orchestration_service` (persist).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable


class LineageNodeKind(StrEnum):
    """The kind of a pulled lineage node — the "downstream isn't always a table" seam.

    ``DATASET`` and ``JOB`` are what the Marquez reference impl emits today.
    ``BI_REPORT`` / ``DASHBOARD`` are **reserved** for a governance catalog (Purview /
    DataHub) that ingests BI lineage — they round-trip on the graph the moment such a
    provider lands, with no change here. ``UNKNOWN`` is the catch-all so an unrecognized
    node type from any catalog is *parsed, not crashed* (:meth:`coerce`).
    """

    DATASET = "dataset"
    JOB = "job"
    BI_REPORT = "bi_report"
    DASHBOARD = "dashboard"
    UNKNOWN = "unknown"

    @classmethod
    def coerce(cls, raw: str | None) -> LineageNodeKind:
        """Map a provider's raw node-type string to a kind, defaulting to ``UNKNOWN``.

        Case-insensitive and tolerant of ``None`` / junk — an unmodelled kind must
        never raise (the node-kind contract). Marquez's ``DATASET`` / ``JOB`` map
        directly; ``DATASET_FIELD`` / ``RUN`` and anything else fall to ``UNKNOWN``.
        """
        if not isinstance(raw, str):
            return cls.UNKNOWN
        token = raw.strip().lower()
        for kind in cls:
            if kind.value == token:
                return kind
        return cls.UNKNOWN


@dataclass(frozen=True)
class LineageNode:
    """One node in a pulled lineage graph.

    ``node_id`` is the provider's stable node key (Marquez's ``dataset:{ns}:{name}``) —
    the adjacency key edges reference, opaque on purpose (an OpenLineage namespace can
    itself contain ``:``, so the id string is never re-parsed for identity).
    ``namespace`` / ``name`` are the OpenLineage identity — set for ``DATASET`` nodes
    (the join key into `assets`), ``None`` for kinds without a dataset identity
    (jobs, and BI/dashboard nodes until the asset model grows a kind).
    """

    node_id: str
    kind: LineageNodeKind
    namespace: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class LineageGraph:
    """A normalized, directed lineage graph: nodes keyed by ``node_id`` + upstream→
    downstream edges (both endpoints are ``node_id`` keys present in ``nodes``).

    Provider-agnostic and kind-carrying — the graph holds ``JOB`` and (future) BI
    nodes, not only datasets. Collapsing jobs to dataset→dataset `lineage_edges` is the
    cache writer's job (`lineage.pull`), not the seam's.
    """

    nodes: dict[str, LineageNode]
    edges: tuple[tuple[str, str], ...]

    @classmethod
    def empty(cls) -> LineageGraph:
        """The empty graph — a fail-soft provider returns this, never raises."""
        return cls(nodes={}, edges=())


@runtime_checkable
class LineageProvider(Protocol):
    """Provider-agnostic catalog-pull interface — Marquez is the reference impl.

    ``provider`` is the stable source tag stamped on pulled `lineage_edges`
    (``'marquez'``) — the prune scope that keeps one source's refresh from ever
    touching another's edges.
    """

    provider: str

    def get_lineage(self, *, namespace: str, name: str, depth: int) -> LineageGraph:
        """Pull the lineage graph around the dataset ``namespace``/``name`` out to
        ``depth`` hops, normalized to a :class:`LineageGraph`.

        **Fail-soft, never raises**: a dead/slow catalog, a garbage payload, or a
        node with no identity yields :meth:`LineageGraph.empty` (or drops the bad
        node), logged — pull is a browse/reason convenience, never a liveness path.
        """
        ...
