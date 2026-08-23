"""Marquez `LineageProvider` reference implementation (ADR 0034, #762)."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from backend.app.core.logging import get_logger
from backend.app.lineage.provider import (
    LineageGraph,
    LineageNode,
    LineageNodeKind,
    LineageUnavailableError,
)

log = get_logger(__name__)

# Bounded read timeout (seconds) — mirrors the emitter's `_EMIT_TIMEOUT_SECONDS`, so a
# degraded Marquez can't stall the refresh task.
_TIMEOUT_SECONDS = 5.0

# Depth guard: Marquez counts hops; clamp to a sane window so a misconfigured caller
# can't ask for an unbounded traversal. 1 ≤ depth ≤ 20.
_MIN_DEPTH = 1
_MAX_DEPTH = 20

# Node cap: truncate a runaway graph rather than materialize an unbounded response into
# memory. Well above any realistic blast-radius neighbourhood for a single dataset.
_MAX_NODES = 10_000

# Dataset-listing pagination (#823).
_DATASET_PAGE = 100
_MAX_DATASETS = 10_000


class MarquezLineageProvider:
    """Pulls lineage from a Marquez server's ``/api/v1/lineage`` API."""

    provider = "marquez"

    def __init__(self, base_url: str, *, timeout: float = _TIMEOUT_SECONDS) -> None:
        # Trailing slash stripped so the path join is unambiguous.
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def list_datasets(self, *, namespace: str) -> list[str]:
        """Every dataset name Marquez holds in ``namespace`` (``GET .../datasets``)."""
        url = f"{self._base_url}/api/v1/namespaces/{quote(namespace, safe='')}/datasets"
        names: list[str] = []
        offset = 0
        while offset < _MAX_DATASETS:
            try:
                response = httpx.get(
                    url,
                    params={"limit": _DATASET_PAGE, "offset": offset},
                    timeout=self._timeout,
                )
                if response.status_code == 404:
                    # The catalog has never heard of this namespace.
                    return []
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("marquez_dataset_list_failed", namespace=namespace, error=str(exc))
                raise LineageUnavailableError(
                    f"marquez dataset listing failed for {namespace}: {exc}"
                ) from exc

            page = payload.get("datasets") if isinstance(payload, dict) else None
            if not isinstance(page, list) or not page:
                break
            for entry in page:
                # Tolerant per-entry (the get_lineage contract): a malformed dataset is
                # dropped, never raised — one bad row must not blind the whole pull.
                if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                    names.append(entry["name"])
            # Advance on the PAGE length, never on how many names we managed to keep.
            if len(page) < _DATASET_PAGE:
                break
            offset += len(page)

        if offset >= _MAX_DATASETS:
            log.warning("marquez_dataset_list_truncated", namespace=namespace, cap=_MAX_DATASETS)
        return names

    def get_lineage(self, *, namespace: str, name: str, depth: int) -> LineageGraph:
        """Pull + normalize the lineage graph around ``dataset:{namespace}:{name}``."""
        node_id = f"dataset:{namespace}:{name}"
        params: dict[str, str | int] = {"nodeId": node_id, "depth": _clamp_depth(depth)}
        try:
            response = httpx.get(
                f"{self._base_url}/api/v1/lineage", params=params, timeout=self._timeout
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("marquez_lineage_pull_failed", node_id=node_id, error=str(exc))
            raise LineageUnavailableError(f"marquez pull failed for {node_id}: {exc}") from exc
        return _parse_graph(payload, seed_node_id=node_id)


def _clamp_depth(depth: int) -> int:
    """Clamp a requested depth into ``[_MIN_DEPTH, _MAX_DEPTH]`` (adversarial guard)."""
    if depth < _MIN_DEPTH:
        return _MIN_DEPTH
    return min(depth, _MAX_DEPTH)


def _parse_graph(payload: Any, *, seed_node_id: str) -> LineageGraph:
    """Normalize a Marquez lineage response into a :class:`LineageGraph`."""
    if not isinstance(payload, dict):
        return LineageGraph.empty()
    raw_graph = payload.get("graph")
    if not isinstance(raw_graph, list):
        return LineageGraph.empty()

    nodes: dict[str, LineageNode] = {}
    for entry in raw_graph:
        if len(nodes) >= _MAX_NODES:
            log.warning("marquez_lineage_graph_truncated", seed=seed_node_id, cap=_MAX_NODES)
            break
        node = _parse_node(entry)
        if node is not None:
            nodes[node.node_id] = node

    edges: set[tuple[str, str]] = set()
    for entry in raw_graph:
        if not isinstance(entry, dict):
            continue
        for key in ("outEdges", "inEdges"):
            for edge in entry.get(key) or []:
                pair = _parse_edge(edge, nodes)
                if pair is not None:
                    edges.add(pair)

    return LineageGraph(nodes=nodes, edges=tuple(sorted(edges)))


def _parse_node(entry: Any) -> LineageNode | None:
    """One graph entry → a :class:`LineageNode`, or ``None`` when unusable."""
    if not isinstance(entry, dict):
        return None
    node_id = entry.get("id")
    if not isinstance(node_id, str) or not node_id:
        return None
    kind = LineageNodeKind.coerce(entry.get("type"))
    namespace, name = (
        _dataset_identity(entry.get("data")) if kind is LineageNodeKind.DATASET else (None, None)
    )
    return LineageNode(node_id=node_id, kind=kind, namespace=namespace, name=name)


def _dataset_identity(data: Any) -> tuple[str | None, str | None]:
    """Pull ``(namespace, name)`` from a node's ``data`` (dataset nodes only)."""
    if not isinstance(data, dict):
        return None, None
    namespace = data.get("namespace")
    name = data.get("name")
    nested = data.get("id")
    if isinstance(nested, dict):
        # An empty string is "missing" too — it must not shadow a valid nested id.
        namespace = (
            namespace if isinstance(namespace, str) and namespace else nested.get("namespace")
        )
        name = name if isinstance(name, str) and name else nested.get("name")
    ns = namespace if isinstance(namespace, str) and namespace else None
    nm = name if isinstance(name, str) and name else None
    return ns, nm


def _parse_edge(edge: Any, nodes: dict[str, LineageNode]) -> tuple[str, str] | None:
    """A Marquez ``{origin, destination}`` edge → a directed ``(upstream, downstream)``
    pair, or ``None`` when malformed or referencing a node not in ``nodes``.
    """
    if not isinstance(edge, dict):
        return None
    origin = edge.get("origin")
    destination = edge.get("destination")
    if not isinstance(origin, str) or not isinstance(destination, str):
        return None
    if origin == destination or origin not in nodes or destination not in nodes:
        return None
    return origin, destination
