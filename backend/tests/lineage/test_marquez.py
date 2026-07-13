"""`lineage.marquez` contract + adversarial tests — mocked HTTP, no live Marquez.

Monkeypatches the module-level ``httpx.get`` (the repo's `orchestration.adf` test
convention). Covers the happy-path parse + job-collapse of a real-shaped Marquez
`/api/v1/lineage` response, and the adversarial battery the seam promises to survive:
garbage payloads, non-200s, transport failure, huge graphs (node cap), cyclic graphs,
unknown node kinds, missing namespaces, and dangling edges.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from backend.app.lineage import marquez as marquez_mod
from backend.app.lineage.marquez import MarquezLineageProvider
from backend.app.lineage.provider import LineageNodeKind, LineageUnavailableError

# A monitored dataset whose OpenLineage namespace itself contains ':' — proves the
# parser reads identity from `data`, never by splitting the node-id string.
_NS = "snowflake://acct.us-west-1.aws"
_SEED = "DATAQ_DB.RETAIL.ORDERS_HEADER"
_DOWN = "DATAQ_DB.ANALYTICS_STG.STG_ORDERS"

_SEED_ID = f"dataset:{_NS}:{_SEED}"
_DOWN_ID = f"dataset:{_NS}:{_DOWN}"
_JOB_ID = "job:dataq:suite.abc123"


class _FakeResponse:
    def __init__(self, *, json_body: Any = None, status_code: int = 200, json_error: bool = False):
        self._json = json_body
        self._json_error = json_error
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error", request=httpx.Request("GET", "http://x"), response=httpx.Response(500)
            )

    def json(self) -> Any:
        if self._json_error:
            raise ValueError("not json")
        return self._json


def _dataset_node(node_id: str, namespace: str, name: str, **edges: Any) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "DATASET",
        "data": {"type": "DB_TABLE", "namespace": namespace, "name": name},
        "inEdges": edges.get("inEdges", []),
        "outEdges": edges.get("outEdges", []),
    }


def _edge(origin: str, destination: str) -> dict[str, str]:
    return {"origin": origin, "destination": destination}


def _bipartite_graph() -> dict[str, Any]:
    """SEED --> job --> DOWN, the standard Marquez dataset↔job shape."""
    return {
        "graph": [
            _dataset_node(_SEED_ID, _NS, _SEED, outEdges=[_edge(_SEED_ID, _JOB_ID)]),
            {
                "id": _JOB_ID,
                "type": "JOB",
                "data": {"type": "BATCH", "namespace": "dataq", "name": "suite.abc123"},
                "inEdges": [_edge(_SEED_ID, _JOB_ID)],
                "outEdges": [_edge(_JOB_ID, _DOWN_ID)],
            },
            _dataset_node(_DOWN_ID, _NS, _DOWN, inEdges=[_edge(_JOB_ID, _DOWN_ID)]),
        ]
    }


def _patch_get(monkeypatch: pytest.MonkeyPatch, response: _FakeResponse) -> dict[str, Any]:
    """Patch `httpx.get` to return ``response``; return the captured call kwargs."""
    seen: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        seen["url"] = url
        seen["params"] = kwargs.get("params")
        seen["timeout"] = kwargs.get("timeout")
        return response

    monkeypatch.setattr(httpx, "get", fake_get)
    return seen


def _provider() -> MarquezLineageProvider:
    return MarquezLineageProvider("http://marquez:5000/")


# ─────────────────────────────── happy path ────────────────────────────────


def test_parses_nodes_and_kinds(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, _FakeResponse(json_body=_bipartite_graph()))
    graph = _provider().get_lineage(namespace=_NS, name=_SEED, depth=3)

    assert set(graph.nodes) == {_SEED_ID, _JOB_ID, _DOWN_ID}
    assert graph.nodes[_SEED_ID].kind is LineageNodeKind.DATASET
    assert graph.nodes[_SEED_ID].namespace == _NS
    assert graph.nodes[_SEED_ID].name == _SEED
    assert graph.nodes[_JOB_ID].kind is LineageNodeKind.JOB
    # job node carries no dataset identity
    assert graph.nodes[_JOB_ID].namespace is None
    # directed edges present (origin -> destination), both endpoints known
    assert (_SEED_ID, _JOB_ID) in graph.edges
    assert (_JOB_ID, _DOWN_ID) in graph.edges


def test_request_shape_nodeid_and_url(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch_get(monkeypatch, _FakeResponse(json_body=_bipartite_graph()))
    _provider().get_lineage(namespace=_NS, name=_SEED, depth=3)

    assert seen["url"] == "http://marquez:5000/api/v1/lineage"  # trailing slash stripped
    assert seen["params"]["nodeId"] == f"dataset:{_NS}:{_SEED}"
    assert seen["params"]["depth"] == 3
    assert seen["timeout"] == marquez_mod._TIMEOUT_SECONDS


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(0, 1), (-5, 1), (1, 1), (7, 7), (999, marquez_mod._MAX_DEPTH)],
)
def test_depth_is_clamped(monkeypatch: pytest.MonkeyPatch, requested: int, expected: int) -> None:
    seen = _patch_get(monkeypatch, _FakeResponse(json_body={"graph": []}))
    _provider().get_lineage(namespace=_NS, name=_SEED, depth=requested)
    assert seen["params"]["depth"] == expected


# ───────────────────────────── adversarial battery ─────────────────────────


def test_transport_error_raises_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    # Outage ≠ empty graph: unavailable must NOT look like "no lineage", or the
    # refresh would prune the whole cache on a dead catalog (review finding, #776).
    def boom(url: str, **kwargs: Any) -> _FakeResponse:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", boom)
    with pytest.raises(LineageUnavailableError):
        _provider().get_lineage(namespace=_NS, name=_SEED, depth=3)


def test_non_200_raises_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, _FakeResponse(status_code=503))
    with pytest.raises(LineageUnavailableError):
        _provider().get_lineage(namespace=_NS, name=_SEED, depth=3)


def test_garbage_json_raises_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, _FakeResponse(json_error=True))
    with pytest.raises(LineageUnavailableError):
        _provider().get_lineage(namespace=_NS, name=_SEED, depth=3)


def test_empty_string_identity_falls_back_to_nested_id(monkeypatch: pytest.MonkeyPatch) -> None:
    # An empty top-level namespace/name must not shadow the nested data.id fallback.
    payload = {
        "graph": [
            {
                "id": "dataset:mz://x:A",
                "type": "DATASET",
                "data": {"namespace": "", "name": "", "id": {"namespace": "mz://x", "name": "A"}},
                "inEdges": [],
                "outEdges": [],
            }
        ]
    }
    _patch_get(monkeypatch, _FakeResponse(json_body=payload))
    graph = _provider().get_lineage(namespace=_NS, name=_SEED, depth=3)
    node = graph.nodes["dataset:mz://x:A"]
    assert (node.namespace, node.name) == ("mz://x", "A")


@pytest.mark.parametrize("payload", [[], "nope", 42, None, {"graph": "not-a-list"}, {}])
def test_structurally_invalid_payloads_return_empty(
    monkeypatch: pytest.MonkeyPatch, payload: Any
) -> None:
    _patch_get(monkeypatch, _FakeResponse(json_body=payload))
    graph = _provider().get_lineage(namespace=_NS, name=_SEED, depth=3)
    assert graph.nodes == {} and graph.edges == ()


def test_malformed_nodes_skipped_valid_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "graph": [
            "not-a-dict",
            {"type": "DATASET"},  # no id
            {"id": "", "type": "DATASET"},  # blank id
            _dataset_node(_SEED_ID, _NS, _SEED),
        ]
    }
    _patch_get(monkeypatch, _FakeResponse(json_body=payload))
    graph = _provider().get_lineage(namespace=_NS, name=_SEED, depth=3)
    assert set(graph.nodes) == {_SEED_ID}


def test_unknown_node_kind_parsed_not_crashed(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "graph": [
            {"id": "bi:pbi:report.sales", "type": "BI_DASHBOARD", "data": {"name": "x"}},
            {"id": "x:y:z", "type": "RUN", "data": {}},
        ]
    }
    _patch_get(monkeypatch, _FakeResponse(json_body=payload))
    graph = _provider().get_lineage(namespace=_NS, name=_SEED, depth=3)
    assert graph.nodes["bi:pbi:report.sales"].kind is LineageNodeKind.UNKNOWN
    assert graph.nodes["x:y:z"].kind is LineageNodeKind.UNKNOWN


def test_cyclic_graph_does_not_hang(monkeypatch: pytest.MonkeyPatch) -> None:
    a, b = "dataset:ns:A", "dataset:ns:B"
    payload = {
        "graph": [
            _dataset_node(a, "ns", "A", outEdges=[_edge(a, b)], inEdges=[_edge(b, a)]),
            _dataset_node(b, "ns", "B", outEdges=[_edge(b, a)], inEdges=[_edge(a, b)]),
        ]
    }
    _patch_get(monkeypatch, _FakeResponse(json_body=payload))
    graph = _provider().get_lineage(namespace="ns", name="A", depth=3)
    assert (a, b) in graph.edges and (b, a) in graph.edges


def test_dangling_edge_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "graph": [
            _dataset_node(_SEED_ID, _NS, _SEED, outEdges=[_edge(_SEED_ID, "job:gone:x")]),
        ]
    }
    _patch_get(monkeypatch, _FakeResponse(json_body=payload))
    graph = _provider().get_lineage(namespace=_NS, name=_SEED, depth=3)
    assert graph.edges == ()  # neighbour node absent → edge dropped


def test_missing_namespace_leaves_identity_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"graph": [{"id": _SEED_ID, "type": "DATASET", "data": {"name": _SEED}}]}
    _patch_get(monkeypatch, _FakeResponse(json_body=payload))
    node = _provider().get_lineage(namespace=_NS, name=_SEED, depth=3).nodes[_SEED_ID]
    assert node.namespace is None and node.name == _SEED


def test_nested_data_id_identity_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "graph": [
            {"id": _SEED_ID, "type": "DATASET", "data": {"id": {"namespace": _NS, "name": _SEED}}}
        ]
    }
    _patch_get(monkeypatch, _FakeResponse(json_body=payload))
    node = _provider().get_lineage(namespace=_NS, name=_SEED, depth=3).nodes[_SEED_ID]
    assert node.namespace == _NS and node.name == _SEED


def test_huge_graph_truncated_at_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(marquez_mod, "_MAX_NODES", 3)
    payload = {"graph": [_dataset_node(f"dataset:ns:t{i}", "ns", f"t{i}") for i in range(50)]}
    _patch_get(monkeypatch, _FakeResponse(json_body=payload))
    graph = _provider().get_lineage(namespace="ns", name="t0", depth=3)
    assert len(graph.nodes) == 3


# ── list_datasets (#823) ─────────────────────────────────────────────────────
#
# The catalog enumeration the pull now seeds from. It had no tests at all in the first
# cut of #823, and that is exactly where a non-terminating loop was hiding.


class _Resp:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)  # type: ignore[arg-type]

    def json(self) -> Any:
        return self._payload


def test_list_datasets_paginates(monkeypatch: Any) -> None:
    pages = {
        0: {"datasets": [{"name": f"DB.S.T{i}"} for i in range(100)]},
        100: {"datasets": [{"name": "DB.S.LAST"}]},
    }
    seen: list[int] = []

    def fake_get(url: str, **kw: Any) -> Any:
        offset = kw["params"]["offset"]
        seen.append(offset)
        return _Resp(pages.get(offset, {"datasets": []}))

    monkeypatch.setattr(httpx, "get", fake_get)
    names = MarquezLineageProvider("http://mz").list_datasets(namespace="snowflake://a")

    assert len(names) == 101
    assert names[-1] == "DB.S.LAST"
    assert seen == [0, 100]  # advanced by the PAGE length, then stopped on a short page


def test_list_datasets_terminates_when_no_entry_parses(monkeypatch: Any) -> None:
    """The non-terminating loop the review caught.

    A server returning full pages whose rows we can't parse (e.g. the name only under a
    nested `id`) would make our name list never grow. Paging on `len(names)` would then
    loop forever — an unbounded run of 5 s HTTP calls that hangs the refresh task.
    Paging on the PAGE length is what bounds it.
    """
    calls = {"n": 0}

    def fake_get(url: str, **kw: Any) -> Any:
        calls["n"] += 1
        assert calls["n"] < 500, "list_datasets did not terminate"
        # Always a FULL page, and not one entry has a top-level `name`.
        return _Resp({"datasets": [{"id": {"name": "X"}} for _ in range(100)]})

    monkeypatch.setattr(httpx, "get", fake_get)
    names = MarquezLineageProvider("http://mz").list_datasets(namespace="snowflake://a")

    assert names == []
    assert calls["n"] == 100  # bounded by _MAX_DATASETS / _DATASET_PAGE, not infinite


def test_an_unknown_namespace_is_empty_not_an_outage(monkeypatch: Any) -> None:
    """A 404 means "the catalog has no such namespace" — an observation, not a failure.

    Raising `unavailable` here would permanently suppress the stale-edge prune for any
    workspace with an asset the catalog doesn't cover (an S3 bucket while only dbt is
    emitting — i.e. most of them), and would cry outage on every cycle.
    """
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _Resp({}, status=404))
    assert MarquezLineageProvider("http://mz").list_datasets(namespace="s3://nope") == []


def test_a_transport_failure_is_unavailable(monkeypatch: Any) -> None:
    def boom(url: str, **kw: Any) -> Any:
        raise httpx.ConnectError("dead")

    monkeypatch.setattr(httpx, "get", boom)
    with pytest.raises(LineageUnavailableError):
        MarquezLineageProvider("http://mz").list_datasets(namespace="snowflake://a")


def test_a_malformed_entry_is_dropped_not_raised(monkeypatch: Any) -> None:
    payload = {"datasets": [{"name": "DB.S.OK"}, {"no_name": 1}, "garbage", None, {"name": 42}]}
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _Resp(payload))
    assert MarquezLineageProvider("http://mz").list_datasets(namespace="snowflake://a") == [
        "DB.S.OK"
    ]
