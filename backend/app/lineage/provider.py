"""The `LineageProvider` seam — pull a lineage graph from a catalog (ADR 0034, #762)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable


class LineageNodeKind(StrEnum):
    """The kind of a pulled lineage node — the "downstream isn't always a table" seam."""

    DATASET = "dataset"
    JOB = "job"
    BI_REPORT = "bi_report"
    DASHBOARD = "dashboard"
    UNKNOWN = "unknown"

    @classmethod
    def coerce(cls, raw: str | None) -> LineageNodeKind:
        """Map a provider's raw node-type string to a kind, defaulting to ``UNKNOWN``."""
        if not isinstance(raw, str):
            return cls.UNKNOWN
        token = raw.strip().lower()
        for kind in cls:
            if kind.value == token:
                return kind
        return cls.UNKNOWN


@dataclass(frozen=True)
class LineageNode:
    """One node in a pulled lineage graph."""

    node_id: str
    kind: LineageNodeKind
    namespace: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class LineageGraph:
    """A normalized, directed lineage graph: nodes keyed by ``node_id`` + upstream→
    downstream edges (both endpoints are ``node_id`` keys present in ``nodes``).
    """

    nodes: dict[str, LineageNode]
    edges: tuple[tuple[str, str], ...]

    @classmethod
    def empty(cls) -> LineageGraph:
        """The empty graph — a *successful* pull that found nothing."""
        return cls(nodes={}, edges=())


class LineageUnavailableError(RuntimeError):
    """The catalog could not be consulted (transport error, non-2xx, garbage body)."""


@runtime_checkable
class LineageProvider(Protocol):
    """Provider-agnostic catalog-pull interface — Marquez is the reference impl."""

    provider: str

    def list_datasets(self, *, namespace: str) -> list[str]:
        """Every dataset name the catalog holds in ``namespace``."""
        ...

    def get_lineage(self, *, namespace: str, name: str, depth: int) -> LineageGraph:
        """Pull the lineage graph around the dataset ``namespace``/``name`` out to
        ``depth`` hops, normalized to a :class:`LineageGraph`.
        """
        ...
