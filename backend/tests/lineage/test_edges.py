"""`lineage.edges` tests against a real Postgres (db_session).

Covers the full dbt-lineage refresh: namespace anchoring off existing assets,
asset materialization for every manifest node, edge upsert, the AC convergence
scenario (manifest v1 → v2: new edges appear, removed edges are pruned and vanish
from blast radius), depth-capped blast radius, the no-anchor fail-soft skip, and
env-preferred anchoring. Skips without TEST_DATABASE_URL.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, cast

from sqlalchemy import select

from backend.app.db.models import Asset, Connection, LineageEdge, User
from backend.app.lineage import edges as edges_mod
from backend.app.lineage.dbt_manifest import ManifestGraph, parse_manifest
from backend.app.lineage.edges import downstream_assets, refresh_dbt_edges, upstream_assets

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_NS = "snowflake://acct"

# Canonicalized OL names for the harness graph (Snowflake upper-folding).
_ORDERS_HEADER = "DATAQ_DB.RETAIL.ORDERS_HEADER"
_STG_ORDERS = "DATAQ_DB.ANALYTICS_STG.STG_ORDERS"
_STG_PRODUCTS = "DATAQ_DB.ANALYTICS_STG.STG_PRODUCTS"
_STG_ORDER_LINES = "DATAQ_DB.ANALYTICS_STG.STG_ORDER_LINES"
_MART_REVENUE = "DATAQ_DB.ANALYTICS.MART_ORDER_REVENUE"
_MART_CUSTOMER = "DATAQ_DB.ANALYTICS.MART_CUSTOMER_ORDERS"
_MART_PRODUCT_SALES = "DATAQ_DB.ANALYTICS.MART_PRODUCT_SALES"


def _graph(version: str) -> Any:
    return parse_manifest((_FIXTURES / f"dbt_manifest_{version}.json").read_bytes())


def _connection(db_session: Any, *, env: str = "dev") -> Connection:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@ex")
    db_session.add(user)
    db_session.flush()
    conn = Connection(
        name=f"dbt-{uuid.uuid4().hex[:8]}",
        type="dbt",
        env=env,
        config={"project_name": "dataq_lineage", "artifacts_uri": "file:///x", "jobs": ["j"]},
        secret_ref="kv-x",
        created_by=user.id,
    )
    db_session.add(conn)
    db_session.commit()
    return conn


def _anchor(db_session: Any, *, name: str, namespace: str = _NS, env: str = "dev") -> Asset:
    asset = Asset(namespace=namespace, name=name, env=env)
    db_session.add(asset)
    db_session.commit()
    return asset


def _asset_id(db_session: Any, name: str, *, namespace: str = _NS) -> uuid.UUID:
    aid = db_session.scalar(
        select(Asset.id).where(Asset.namespace == namespace, Asset.name == name)
    )
    assert aid is not None, f"asset {name!r} not found under {namespace!r}"
    return cast(uuid.UUID, aid)


def _edge_exists(db_session: Any, up: uuid.UUID, down: uuid.UUID) -> bool:
    return (
        db_session.scalar(
            select(LineageEdge.id).where(
                LineageEdge.upstream_asset_id == up,
                LineageEdge.downstream_asset_id == down,
                LineageEdge.source == "dbt",
            )
        )
        is not None
    )


# ── first refresh: anchor, materialize, edges ─────────────────────────────────


def test_refresh_creates_all_nodes_and_edges_under_anchor(db_session: Any) -> None:
    conn = _connection(db_session, env="dev")
    _anchor(db_session, name=_ORDERS_HEADER, env="dev")  # seeds the namespace

    live = refresh_dbt_edges(db_session, connection=conn, graph=_graph("v1"))

    assert live == 8
    assets = db_session.scalars(select(Asset).where(Asset.namespace == _NS)).all()
    assert len(assets) == 10  # every manifest node materialized under the anchor
    # every asset carries the connection's env + provenance connection_id
    materialized = {a.name: a for a in assets}
    assert materialized[_STG_ORDERS].env == "dev"
    assert materialized[_STG_ORDERS].connection_id == conn.id
    assert db_session.scalar(select(LineageEdge).where(LineageEdge.source == "dbt")) is not None


# ── the AC: v1 → v2 convergence (add + remove edges, new model) ───────────────


def test_refresh_converges_on_manifest_change(db_session: Any) -> None:
    conn = _connection(db_session, env="dev")
    _anchor(db_session, name=_ORDERS_HEADER, env="dev")

    assert refresh_dbt_edges(db_session, connection=conn, graph=_graph("v1")) == 8
    # v2: +stg_products->mart_order_revenue, +mart_product_sales (+2 edges),
    #     -stg_order_lines->mart_order_revenue = 10 live edges.
    assert refresh_dbt_edges(db_session, connection=conn, graph=_graph("v2")) == 10

    stg_products = _asset_id(db_session, _STG_PRODUCTS)
    stg_order_lines = _asset_id(db_session, _STG_ORDER_LINES)
    mart_revenue = _asset_id(db_session, _MART_REVENUE)
    mart_new = _asset_id(db_session, _MART_PRODUCT_SALES)

    # New edge appeared; new model materialized.
    assert mart_new is not None
    assert _edge_exists(db_session, stg_products, mart_revenue)
    assert _edge_exists(db_session, stg_products, mart_new)
    assert _edge_exists(db_session, stg_order_lines, mart_new)

    # Removed edge is pruned and GONE from the blast radius.
    assert not _edge_exists(db_session, stg_order_lines, mart_revenue)
    down_names = {a.name for a in downstream_assets(db_session, stg_order_lines)}
    assert _MART_REVENUE not in down_names
    assert _MART_PRODUCT_SALES in down_names


# ── blast radius (depth-capped BFS) ───────────────────────────────────────────


def test_downstream_blast_radius_and_depth_cap(db_session: Any) -> None:
    conn = _connection(db_session, env="dev")
    _anchor(db_session, name=_ORDERS_HEADER, env="dev")
    refresh_dbt_edges(db_session, connection=conn, graph=_graph("v1"))

    orders_header = _asset_id(db_session, _ORDERS_HEADER)
    full = {a.name for a in downstream_assets(db_session, orders_header)}
    assert full == {_STG_ORDERS, _MART_REVENUE, _MART_CUSTOMER}

    # Depth cap: one hop reaches only the staging view.
    depth1 = {a.name for a in downstream_assets(db_session, orders_header, max_depth=1)}
    assert depth1 == {_STG_ORDERS}


def test_upstream_provenance(db_session: Any) -> None:
    conn = _connection(db_session, env="dev")
    _anchor(db_session, name=_ORDERS_HEADER, env="dev")
    refresh_dbt_edges(db_session, connection=conn, graph=_graph("v1"))

    mart_revenue = _asset_id(db_session, _MART_REVENUE)
    ups = {a.name for a in upstream_assets(db_session, mart_revenue)}
    # mart_order_revenue ← stg_order_lines, stg_orders ← order_lines, orders_header.
    assert _STG_ORDERS in ups
    assert _STG_ORDER_LINES in ups
    assert _ORDERS_HEADER in ups


# ── fail-soft: no namespace anchor ────────────────────────────────────────────


def test_no_anchor_returns_none_and_writes_nothing(db_session: Any) -> None:
    conn = _connection(db_session, env="dev")
    # No asset whose name is in the manifest → no namespace to anchor under.

    result = refresh_dbt_edges(db_session, connection=conn, graph=_graph("v1"))

    assert result is None
    assert db_session.scalars(select(LineageEdge)).all() == []
    # No manifest node was materialized either (the skip happens before upserts).
    assert db_session.scalar(select(Asset).where(Asset.name == _STG_ORDERS)) is None


# ── env-preferred anchoring ───────────────────────────────────────────────────


def test_env_matching_anchor_wins(db_session: Any) -> None:
    conn = _connection(db_session, env="prod")
    # Same table name resolved under two namespaces in two envs; the connection's
    # env (prod) must pick the prod namespace.
    _anchor(db_session, name=_ORDERS_HEADER, namespace="snowflake://dev-acct", env="dev")
    _anchor(db_session, name=_ORDERS_HEADER, namespace="snowflake://prod-acct", env="prod")

    refresh_dbt_edges(db_session, connection=conn, graph=_graph("v1"))

    # Manifest-only node was filed under the prod namespace.
    ns = db_session.scalar(select(Asset.namespace).where(Asset.name == _STG_PRODUCTS))
    assert ns == "snowflake://prod-acct"


# ── fail-open contract ────────────────────────────────────────────────────────


def test_refresh_never_raises_on_internal_error(db_session: Any, monkeypatch: Any) -> None:
    conn = _connection(db_session, env="dev")
    _anchor(db_session, name=_ORDERS_HEADER, env="dev")

    def _boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("db exploded mid-refresh")

    monkeypatch.setattr(edges_mod, "upsert_assets", _boom)

    # A DB hiccup after anchoring must be swallowed (returns None), not raised.
    assert refresh_dbt_edges(db_session, connection=conn, graph=_graph("v1")) is None
    assert db_session.scalars(select(LineageEdge)).all() == []


def test_empty_graph_returns_none(db_session: Any) -> None:
    conn = _connection(db_session, env="dev")
    empty = ManifestGraph(adapter_type="snowflake", nodes={}, edges=[])
    assert refresh_dbt_edges(db_session, connection=conn, graph=empty) is None


# ── provenance: prune is connection-scoped, provenance is preserved ───────────


def test_prune_is_connection_scoped_across_projects(db_session: Any) -> None:
    """Two dbt connections sharing table names: A's refresh must NOT prune B's edge.

    The review's cross-project-corruption fix — the prune is scoped by
    `(source, connection_id)`, so a refresh of project A never deletes project B's
    live edges even when both manifests reference the same assets.
    """
    # Orchestrator connections are singletons per (type, env), so the two projects
    # live in different envs; both pin the SAME `lineage_namespace` so they resolve
    # the same shared asset rows regardless of env.
    conn_a = _connection(db_session, env="dev")
    conn_b = _connection(db_session, env="qa")
    for c in (conn_a, conn_b):
        c.config = {**c.config, "lineage_namespace": _NS}
    db_session.commit()

    # B refreshes first and owns its edge set.
    assert refresh_dbt_edges(db_session, connection=conn_b, graph=_graph("v1")) == 8
    # A refreshes the SAME manifest — its own edges, B's must survive untouched.
    assert refresh_dbt_edges(db_session, connection=conn_a, graph=_graph("v1")) == 8

    b_edges = db_session.scalars(
        select(LineageEdge).where(LineageEdge.connection_id == conn_b.id)
    ).all()
    a_edges = db_session.scalars(
        select(LineageEdge).where(LineageEdge.connection_id == conn_a.id)
    ).all()
    assert len(b_edges) == 8  # B's edges NOT pruned by A's refresh
    assert len(a_edges) == 8


def test_dbt_refresh_preserves_datasource_provenance(db_session: Any) -> None:
    """A pre-existing datasource-owned asset keeps its connection_id/env through a
    dbt refresh (provenance-preserving upsert) — not flipped to the dbt connection.
    """
    # A Snowflake datasource connection that "owns" the anchor asset's provenance.
    ds_user = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@ex")
    db_session.add(ds_user)
    db_session.flush()
    ds_conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "acct", "database": "DATAQ_DB"},
        created_by=ds_user.id,
    )
    db_session.add(ds_conn)
    db_session.commit()

    owned = Asset(namespace=_NS, name=_ORDERS_HEADER, env="dev", connection_id=ds_conn.id)
    db_session.add(owned)
    db_session.commit()

    dbt_conn = _connection(db_session, env="dev")
    refresh_dbt_edges(db_session, connection=dbt_conn, graph=_graph("v1"))

    db_session.refresh(owned)
    assert owned.connection_id == ds_conn.id  # NOT flipped to the dbt connection
    assert owned.env == "dev"
    # A manifest-only node (no prior provenance) DOES get the dbt connection.
    stg = db_session.scalar(select(Asset).where(Asset.name == _STG_ORDERS))
    assert stg.connection_id == dbt_conn.id


# ── anchor hardening (#759 review): env-strict, pin, deterministic tie ─────────


def test_no_env_match_skips(db_session: Any) -> None:
    # Connection env=prod, only a dev-env anchor exists → NO cross-env fallback.
    conn = _connection(db_session, env="prod")
    _anchor(db_session, name=_ORDERS_HEADER, env="dev")
    assert refresh_dbt_edges(db_session, connection=conn, graph=_graph("v1")) is None
    assert db_session.scalar(select(Asset).where(Asset.name == _STG_ORDERS)) is None


def test_null_env_asset_still_anchors(db_session: Any) -> None:
    # An env-unknown (NULL) asset is a valid anchor for any connection env.
    conn = _connection(db_session, env="prod")
    db_session.add(Asset(namespace=_NS, name=_ORDERS_HEADER, env=None))
    db_session.commit()
    assert refresh_dbt_edges(db_session, connection=conn, graph=_graph("v1")) == 8


def test_lineage_namespace_pin_bypasses_heuristic(db_session: Any) -> None:
    # `lineage_namespace` on the connection config pins the anchor verbatim, even
    # with NO existing asset to infer from (greenfield project).
    conn = _connection(db_session, env="dev")
    conn.config = {**conn.config, "lineage_namespace": "snowflake://pinned"}
    db_session.commit()
    assert refresh_dbt_edges(db_session, connection=conn, graph=_graph("v1")) == 8
    ns = db_session.scalar(select(Asset.namespace).where(Asset.name == _STG_ORDERS))
    assert ns == "snowflake://pinned"


def test_anchor_tie_break_is_deterministic(db_session: Any, monkeypatch: Any) -> None:
    # Two namespaces tie on majority AND on last_seen → lexicographically-smallest
    # namespace wins, deterministically (never flip-flops between refreshes).
    conn = _connection(db_session, env="dev")
    from datetime import UTC, datetime

    ts = datetime(2026, 7, 1, tzinfo=UTC)
    for ns in ("snowflake://zzz", "snowflake://aaa"):
        a = Asset(namespace=ns, name=_ORDERS_HEADER, env="dev")
        db_session.add(a)
        db_session.flush()
        a.last_seen = ts  # identical timestamps force the lexicographic tie-break
    db_session.commit()

    refresh_dbt_edges(db_session, connection=conn, graph=_graph("v1"))
    ns = db_session.scalar(select(Asset.namespace).where(Asset.name == _STG_ORDERS))
    assert ns == "snowflake://aaa"  # smallest wins


# ── adapter-aware canonicalization (shared with asset_identity) ────────────────


def test_databricks_adapter_folds_lowercase() -> None:
    from backend.app.lineage.dbt_manifest import NodeIdentity
    from backend.app.lineage.edges import _canonical_name

    ident = NodeIdentity(database="Main", schema="Analytics", name="Orders")
    # databricks/spark fold to lower (matching suite-resolved UC assets), snowflake
    # to upper, other adapters verbatim.
    assert _canonical_name("databricks", ident) == "main.analytics.orders"
    assert _canonical_name("spark", ident) == "main.analytics.orders"
    assert _canonical_name("snowflake", ident) == "MAIN.ANALYTICS.ORDERS"
    assert _canonical_name("postgres", ident) == "Main.Analytics.Orders"
