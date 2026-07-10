"""Pure unit tests for the dbt manifest parser (`lineage.dbt_manifest`).

No DB / IO — bytes in, a `ManifestGraph` out. Pins the known harness graph
(10 physical nodes / 8 table-level edges), the tests/operations filter, ephemeral
collapse, the schema-version gate, and the adversarial-input battery (every
malformed payload → `ManifestParseError`, never a bare KeyError/UnicodeDecodeError).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend.app.lineage import dbt_manifest
from backend.app.lineage.dbt_manifest import ManifestParseError, NodeIdentity, parse_manifest

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _load(name: str) -> bytes:
    return (_FIXTURES / name).read_bytes()


def _manifest(
    *,
    nodes: dict[str, Any] | None = None,
    sources: dict[str, Any] | None = None,
    parent_map: dict[str, Any] | None = None,
    version: str = "v12",
    adapter: str = "snowflake",
) -> dict[str, Any]:
    return {
        "metadata": {
            "dbt_schema_version": f"https://schemas.getdbt.com/dbt/manifest/{version}.json",
            "adapter_type": adapter,
        },
        "nodes": nodes if nodes is not None else {},
        "sources": sources if sources is not None else {},
        "parent_map": parent_map if parent_map is not None else {},
    }


def _dumps(doc: dict[str, Any]) -> bytes:
    import json

    return json.dumps(doc).encode()


# ── the known harness graph (fixture v1) ──────────────────────────────────────


def test_parses_harness_graph_node_and_edge_counts() -> None:
    graph = parse_manifest(_load("dbt_manifest_v1.json"))
    assert graph.adapter_type == "snowflake"
    # 4 RETAIL sources + 4 ANALYTICS_STG staging + 2 ANALYTICS marts = 10 physical.
    assert len(graph.nodes) == 10
    assert len(graph.edges) == 8


def test_harness_node_identities_use_alias_and_source_name() -> None:
    graph = parse_manifest(_load("dbt_manifest_v1.json"))
    assert graph.nodes["source.dataq_lineage.retail.orders_header"] == NodeIdentity(
        database="DATAQ_DB", schema="RETAIL", name="orders_header"
    )
    assert graph.nodes["model.dataq_lineage.stg_orders"] == NodeIdentity(
        database="DATAQ_DB", schema="ANALYTICS_STG", name="stg_orders"
    )


def test_harness_edges_are_the_expected_table_level_set() -> None:
    graph = parse_manifest(_load("dbt_manifest_v1.json"))
    p = "model.dataq_lineage"
    s = "source.dataq_lineage.retail"
    assert set(graph.edges) == {
        (f"{s}.products", f"{p}.stg_products"),
        (f"{s}.customers", f"{p}.stg_customers"),
        (f"{s}.orders_header", f"{p}.stg_orders"),
        (f"{s}.order_lines", f"{p}.stg_order_lines"),
        (f"{p}.stg_order_lines", f"{p}.mart_order_revenue"),
        (f"{p}.stg_orders", f"{p}.mart_order_revenue"),
        (f"{p}.stg_customers", f"{p}.mart_customer_orders"),
        (f"{p}.stg_orders", f"{p}.mart_customer_orders"),
    }


def test_tests_and_operations_are_filtered_out() -> None:
    graph = parse_manifest(_load("dbt_manifest_v1.json"))
    # No `test.*` or `operation.*` unique_id survives into the physical node set.
    assert all(
        not uid.startswith("test.") and not uid.startswith("operation.") for uid in graph.nodes
    )


# ── ephemeral collapse ────────────────────────────────────────────────────────


def test_ephemeral_middle_node_collapses_to_physical_ancestor() -> None:
    doc = _manifest(
        nodes={
            "model.p.eph": {
                "resource_type": "model",
                "database": "D",
                "schema": "S",
                "alias": "eph",
                "config": {"materialized": "ephemeral"},
                "relation_name": None,
            },
            "model.p.child": {
                "resource_type": "model",
                "database": "D",
                "schema": "S",
                "alias": "child",
                "config": {"materialized": "view"},
                "relation_name": "D.S.child",
            },
        },
        sources={
            "source.p.src": {
                "database": "D",
                "schema": "RAW",
                "name": "src",
                "relation_name": "D.RAW.src",
            }
        },
        parent_map={
            "model.p.eph": ["source.p.src"],
            "model.p.child": ["model.p.eph"],
            "source.p.src": [],
        },
    )
    graph = parse_manifest(_dumps(doc))
    # The ephemeral node is not physical; its child connects straight to the source.
    assert set(graph.nodes) == {"source.p.src", "model.p.child"}
    assert graph.edges == [("source.p.src", "model.p.child")]


def test_null_relation_name_is_treated_as_ephemeral() -> None:
    doc = _manifest(
        nodes={
            "model.p.mid": {
                "resource_type": "model",
                "database": "D",
                "schema": "S",
                "alias": "mid",
                "config": {"materialized": "view"},
                "relation_name": None,  # no physical relation → collapse
            },
            "model.p.leaf": {
                "resource_type": "model",
                "database": "D",
                "schema": "S",
                "alias": "leaf",
                "config": {"materialized": "table"},
                "relation_name": "D.S.leaf",
            },
        },
        sources={
            "source.p.src": {"database": "D", "schema": "RAW", "name": "src", "relation_name": "x"}
        },
        parent_map={
            "model.p.mid": ["source.p.src"],
            "model.p.leaf": ["model.p.mid"],
            "source.p.src": [],
        },
    )
    graph = parse_manifest(_dumps(doc))
    assert "model.p.mid" not in graph.nodes
    assert graph.edges == [("source.p.src", "model.p.leaf")]


# ── schema-version gate ───────────────────────────────────────────────────────


def test_version_below_minimum_raises() -> None:
    with pytest.raises(ManifestParseError, match="below the minimum"):
        parse_manifest(_dumps(_manifest(version="v9")))


def test_version_in_best_effort_range_parses_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    doc = _manifest(
        nodes={},
        sources={"source.p.s": {"database": "D", "schema": "R", "name": "s", "relation_name": "x"}},
        parent_map={"source.p.s": []},
        version="v11",
    )
    graph = parse_manifest(_dumps(doc))  # v11 parses best-effort (no raise)
    assert set(graph.nodes) == {"source.p.s"}


def test_unparsable_version_raises() -> None:
    doc = _manifest()
    doc["metadata"]["dbt_schema_version"] = "not-a-version"
    with pytest.raises(ManifestParseError, match="unrecognised dbt_schema_version"):
        parse_manifest(_dumps(doc))


@pytest.mark.parametrize(
    "schema_version",
    [
        "https://schemas.getdbt.com/dbt/manifest/v12",  # no `.json` suffix
        "https://schemas.getdbt.com/dbt/manifest/v12.json?cache=1",  # query suffix
        "dbt/manifest/v12",  # bare
    ],
)
def test_version_regex_tolerates_suffix_variants(schema_version: str) -> None:
    # The gate accepts `/v<NN>` with an optional `.json` / trailing suffix (#759
    # review) — a variant URL must not fail the otherwise-supported v12.
    doc = _manifest(
        sources={"source.p.s": {"database": "D", "schema": "R", "name": "s", "relation_name": "x"}},
        parent_map={"source.p.s": []},
    )
    doc["metadata"]["dbt_schema_version"] = schema_version
    graph = parse_manifest(_dumps(doc))
    assert set(graph.nodes) == {"source.p.s"}


def test_shared_ephemeral_chain_resolves_once() -> None:
    # Two physical children selecting from the same ephemeral CTE: both connect to
    # the source through the memoized ephemeral resolution (correct, and walked once).
    doc = _manifest(
        nodes={
            "model.p.eph": {
                "resource_type": "model",
                "database": "D",
                "schema": "S",
                "alias": "eph",
                "config": {"materialized": "ephemeral"},
                "relation_name": None,
            },
            "model.p.a": {
                "resource_type": "model",
                "database": "D",
                "schema": "S",
                "alias": "a",
                "config": {"materialized": "view"},
                "relation_name": "D.S.a",
            },
            "model.p.b": {
                "resource_type": "model",
                "database": "D",
                "schema": "S",
                "alias": "b",
                "config": {"materialized": "view"},
                "relation_name": "D.S.b",
            },
        },
        sources={
            "source.p.src": {"database": "D", "schema": "RAW", "name": "src", "relation_name": "x"}
        },
        parent_map={
            "model.p.eph": ["source.p.src"],
            "model.p.a": ["model.p.eph"],
            "model.p.b": ["model.p.eph"],
            "source.p.src": [],
        },
    )
    graph = parse_manifest(_dumps(doc))
    assert set(graph.edges) == {("source.p.src", "model.p.a"), ("source.p.src", "model.p.b")}


# ── adversarial battery ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        b"",  # empty
        b'{"metadata": {"dbt_schema_version": "v12"',  # truncated
        b"\x00\x01\x02",  # NUL / binary
        b"null",  # valid JSON, wrong type
        b"[1, 2, 3]",  # valid JSON array, not an object
        b"{}",  # object but no metadata
    ],
)
def test_malformed_payloads_raise_manifest_parse_error(raw: bytes) -> None:
    with pytest.raises(ManifestParseError):
        parse_manifest(raw)


def test_non_bytes_payload_raises() -> None:
    with pytest.raises(ManifestParseError, match="must be bytes"):
        parse_manifest("a string")  # type: ignore[arg-type]


def test_missing_parent_map_raises() -> None:
    doc = _manifest()
    del doc["parent_map"]
    with pytest.raises(ManifestParseError, match="'nodes' / 'sources' / 'parent_map'"):
        parse_manifest(_dumps(doc))


def test_missing_metadata_raises() -> None:
    with pytest.raises(ManifestParseError, match="missing 'metadata'"):
        parse_manifest(b'{"nodes": {}, "sources": {}, "parent_map": {}}')


def test_oversized_payload_refused_before_load(monkeypatch: pytest.MonkeyPatch) -> None:
    # Shrink the cap rather than build a 128 MiB blob: proves the guard fires
    # *before* json.loads (a genuinely huge/hostile payload never gets parsed).
    monkeypatch.setattr(dbt_manifest, "_MAX_MANIFEST_BYTES", 4)
    with pytest.raises(ManifestParseError, match=r"above the .* cap"):
        parse_manifest(_dumps(_manifest()))
