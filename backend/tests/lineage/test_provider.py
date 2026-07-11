"""`lineage.provider` seam types — the node-kind contract (ADR 0034, #762).

Pure, no DB / no network: the DTOs and the ``LineageNodeKind.coerce`` tolerance that
makes "downstream isn't always a table" safe (an unmodelled kind parses, never crashes).
"""

from __future__ import annotations

import dataclasses

import pytest

from backend.app.lineage.marquez import MarquezLineageProvider
from backend.app.lineage.provider import (
    LineageGraph,
    LineageNode,
    LineageNodeKind,
    LineageProvider,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("DATASET", LineageNodeKind.DATASET),
        ("dataset", LineageNodeKind.DATASET),
        ("  Job  ", LineageNodeKind.JOB),
        ("bi_report", LineageNodeKind.BI_REPORT),
        ("DASHBOARD", LineageNodeKind.DASHBOARD),
        # Kinds Marquez emits that we don't model, plus junk — all coerce to UNKNOWN,
        # never raise (the node-kind contract).
        ("DATASET_FIELD", LineageNodeKind.UNKNOWN),
        ("RUN", LineageNodeKind.UNKNOWN),
        ("totally-new-catalog-kind", LineageNodeKind.UNKNOWN),
        ("", LineageNodeKind.UNKNOWN),
        (None, LineageNodeKind.UNKNOWN),
        (123, LineageNodeKind.UNKNOWN),
    ],
)
def test_node_kind_coerce_is_total(raw: object, expected: LineageNodeKind) -> None:
    assert LineageNodeKind.coerce(raw) is expected  # type: ignore[arg-type]


def test_empty_graph_is_falsy_and_frozen() -> None:
    graph = LineageGraph.empty()
    assert graph.nodes == {}
    assert graph.edges == ()
    with pytest.raises(dataclasses.FrozenInstanceError):  # frozen — assignment rejected
        graph.nodes = {}  # type: ignore[misc]


def test_node_defaults_leave_identity_unset() -> None:
    node = LineageNode(node_id="job:ns:j", kind=LineageNodeKind.JOB)
    assert node.namespace is None and node.name is None


def test_marquez_impl_satisfies_the_seam_protocol() -> None:
    provider = MarquezLineageProvider("http://marquez:5000")
    # runtime_checkable Protocol — the reference impl structurally conforms.
    assert isinstance(provider, LineageProvider)
    assert provider.provider == "marquez"
