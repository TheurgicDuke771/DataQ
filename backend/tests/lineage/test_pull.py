"""`lineage.pull` — the pull-parse-upsert path against a real Postgres (db_session).

The compose round-trip (emitter → Marquez → pull) is a manual/compose verification
(see docs/orchestration.md); here a **fake `LineageProvider`** returns canned,
real-shaped Marquez graphs so the DB-side contract is exercised without a live catalog:
edges upsert with ``source='marquez'`` + NULL connection_id, coexist with dbt edges
without duplication, dedupe idempotently, prune when they vanish, and fail open.

Skips without TEST_DATABASE_URL.
"""

from __future__ import annotations

import uuid
from typing import Any, cast

import pytest
from sqlalchemy import func, select

from backend.app.db.models import Asset, Connection, LineageEdge, User
from backend.app.lineage import pull as pull_mod
from backend.app.lineage.edges import downstream_assets
from backend.app.lineage.provider import LineageGraph, LineageNode, LineageNodeKind
from backend.app.lineage.pull import get_lineage_provider, refresh_pulled_edges

_NS = "snowflake://acct"
_SEED = "DATAQ_DB.RETAIL.ORDERS_HEADER"
_DOWN = "DATAQ_DB.ANALYTICS_STG.STG_ORDERS"

_SEED_ID = f"dataset:{_NS}:{_SEED}"
_DOWN_ID = f"dataset:{_NS}:{_DOWN}"
_JOB_ID = "job:dataq:suite.abc"


class _FakeProvider:
    """A `LineageProvider` that replays canned graphs keyed on the seed identity."""

    provider = "marquez"

    def __init__(self, graphs: dict[tuple[str, str], LineageGraph]) -> None:
        self._graphs = graphs
        self.calls: list[tuple[str, str, int]] = []

    def get_lineage(self, *, namespace: str, name: str, depth: int) -> LineageGraph:
        self.calls.append((namespace, name, depth))
        return self._graphs.get((namespace, name), LineageGraph.empty())


class _BoomProvider:
    provider = "marquez"

    def get_lineage(self, *, namespace: str, name: str, depth: int) -> LineageGraph:
        raise RuntimeError("catalog on fire")


def _bipartite_graph() -> LineageGraph:
    """SEED --> job --> DOWN, collapsing to a single SEED→DOWN dataset edge."""
    nodes = {
        _SEED_ID: LineageNode(_SEED_ID, LineageNodeKind.DATASET, _NS, _SEED),
        _JOB_ID: LineageNode(_JOB_ID, LineageNodeKind.JOB),
        _DOWN_ID: LineageNode(_DOWN_ID, LineageNodeKind.DATASET, _NS, _DOWN),
    }
    edges = ((_SEED_ID, _JOB_ID), (_JOB_ID, _DOWN_ID))
    return LineageGraph(nodes=nodes, edges=edges)


def _seed_asset(db_session: Any, *, name: str, namespace: str = _NS, env: str = "dev") -> Asset:
    asset = Asset(namespace=namespace, name=name, env=env)
    db_session.add(asset)
    db_session.commit()
    return asset


def _asset_id(db_session: Any, name: str, *, namespace: str = _NS) -> uuid.UUID:
    aid = db_session.scalar(
        select(Asset.id).where(Asset.namespace == namespace, Asset.name == name)
    )
    assert aid is not None, f"asset {name!r} not found"
    return cast(uuid.UUID, aid)


def _marquez_edges(db_session: Any) -> list[LineageEdge]:
    return list(
        db_session.scalars(
            select(LineageEdge).where(
                LineageEdge.source == "marquez", LineageEdge.connection_id.is_(None)
            )
        )
    )


def _dbt_connection(db_session: Any) -> Connection:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@ex")
    db_session.add(user)
    db_session.flush()
    conn = Connection(
        name=f"dbt-{uuid.uuid4().hex[:8]}",
        type="dbt",
        env="dev",
        config={"project_name": "p", "artifacts_uri": "file:///x", "jobs": ["j"]},
        secret_ref="kv-x",
        created_by=user.id,
    )
    db_session.add(conn)
    db_session.commit()
    return conn


# ─────────────────────────────── round-trip ────────────────────────────────


def test_pull_materializes_edge_and_downstream_asset(db_session: Any) -> None:
    _seed_asset(db_session, name=_SEED)
    provider = _FakeProvider({(_NS, _SEED): _bipartite_graph()})

    live = refresh_pulled_edges(db_session, provider=provider)

    assert live == 1
    assert provider.calls == [(_NS, _SEED, pull_mod._PULL_DEPTH)]
    edges = _marquez_edges(db_session)
    assert len(edges) == 1
    assert edges[0].upstream_asset_id == _asset_id(db_session, _SEED)
    # the downstream table DataQ didn't monitor was materialized as an asset (NULL prov)
    down = db_session.scalar(select(Asset).where(Asset.name == _DOWN))
    assert down is not None and down.connection_id is None and down.env is None
    assert edges[0].downstream_asset_id == down.id
    # blast radius now spans the pulled edge
    assert [a.name for a in downstream_assets(db_session, _asset_id(db_session, _SEED))] == [_DOWN]


def test_pulled_and_dbt_edges_coexist_without_duplication(db_session: Any) -> None:
    seed = _seed_asset(db_session, name=_SEED)
    down = _seed_asset(db_session, name=_DOWN)
    conn = _dbt_connection(db_session)
    # a dbt-sourced edge for the SAME physical (SEED -> DOWN) pair
    db_session.add(
        LineageEdge(
            upstream_asset_id=seed.id,
            downstream_asset_id=down.id,
            source="dbt",
            connection_id=conn.id,
        )
    )
    db_session.commit()

    refresh_pulled_edges(db_session, provider=_FakeProvider({(_NS, _SEED): _bipartite_graph()}))

    rows = db_session.scalars(
        select(LineageEdge).where(
            LineageEdge.upstream_asset_id == seed.id,
            LineageEdge.downstream_asset_id == down.id,
        )
    ).all()
    # two distinct provenance rows for the same pair — no merge, no loss
    assert {r.source for r in rows} == {"dbt", "marquez"}
    assert len(rows) == 2
    # blast radius de-dupes the doubled edge to one downstream asset
    assert [a.name for a in downstream_assets(db_session, seed.id)] == [_DOWN]


def test_refresh_is_idempotent(db_session: Any) -> None:
    _seed_asset(db_session, name=_SEED)
    provider = _FakeProvider({(_NS, _SEED): _bipartite_graph()})

    refresh_pulled_edges(db_session, provider=provider)
    first = _marquez_edges(db_session)[0].last_seen
    refresh_pulled_edges(db_session, provider=provider)

    edges = _marquez_edges(db_session)
    assert len(edges) == 1  # partial unique index deduped the re-pull
    assert edges[0].last_seen >= first  # last_seen bumped


def test_prune_drops_vanished_edges_but_not_dbt(db_session: Any) -> None:
    seed = _seed_asset(db_session, name=_SEED)
    down = _seed_asset(db_session, name=_DOWN)
    conn = _dbt_connection(db_session)
    db_session.add(
        LineageEdge(
            upstream_asset_id=seed.id,
            downstream_asset_id=down.id,
            source="dbt",
            connection_id=conn.id,
        )
    )
    db_session.commit()

    refresh_pulled_edges(db_session, provider=_FakeProvider({(_NS, _SEED): _bipartite_graph()}))
    assert len(_marquez_edges(db_session)) == 1

    # next refresh: the catalog no longer reports the edge → pulled row pruned
    refresh_pulled_edges(db_session, provider=_FakeProvider({(_NS, _SEED): LineageGraph.empty()}))

    assert _marquez_edges(db_session) == []
    # the dbt edge is untouched by the marquez prune (provenance-scoped)
    dbt_live = db_session.scalar(
        select(func.count()).select_from(LineageEdge).where(LineageEdge.source == "dbt")
    )
    assert dbt_live == 1


def test_identity_less_dataset_node_is_dropped(db_session: Any) -> None:
    _seed_asset(db_session, name=_SEED)
    # downstream dataset node with no namespace/name → no asset identity → edge dropped
    nodes = {
        _SEED_ID: LineageNode(_SEED_ID, LineageNodeKind.DATASET, _NS, _SEED),
        _DOWN_ID: LineageNode(_DOWN_ID, LineageNodeKind.DATASET, None, None),
    }
    graph = LineageGraph(nodes=nodes, edges=((_SEED_ID, _DOWN_ID),))
    live = refresh_pulled_edges(db_session, provider=_FakeProvider({(_NS, _SEED): graph}))
    assert live == 0
    assert _marquez_edges(db_session) == []


def test_no_seed_assets_returns_none(db_session: Any) -> None:
    provider = _FakeProvider({})
    assert refresh_pulled_edges(db_session, provider=provider) is None
    assert provider.calls == []  # never queried the catalog


def test_provider_exception_fails_open(db_session: Any) -> None:
    _seed_asset(db_session, name=_SEED)
    # a provider that raises (violating the seam contract) must not propagate
    assert refresh_pulled_edges(db_session, provider=_BoomProvider()) is None
    assert _marquez_edges(db_session) == []


# ─────────────────────────── provider factory (gate) ───────────────────────


def test_factory_dark_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_settings(monkeypatch, provider=None, url=None)
    assert get_lineage_provider() is None


def test_factory_marquez_without_url_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_settings(monkeypatch, provider="marquez", url=None)
    assert get_lineage_provider() is None


def test_factory_builds_marquez(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_settings(monkeypatch, provider="marquez", url="http://marquez:5000")
    provider = get_lineage_provider()
    assert provider is not None and provider.provider == "marquez"


def test_factory_unknown_provider_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_settings(monkeypatch, provider="datahub", url="http://x")
    assert get_lineage_provider() is None


def _reset_settings(
    monkeypatch: pytest.MonkeyPatch, *, provider: str | None, url: str | None
) -> None:
    from backend.app.core.config import get_settings

    if provider is None:
        monkeypatch.delenv("LINEAGE_PROVIDER", raising=False)
    else:
        monkeypatch.setenv("LINEAGE_PROVIDER", provider)
    if url is None:
        monkeypatch.delenv("MARQUEZ_URL", raising=False)
    else:
        monkeypatch.setenv("MARQUEZ_URL", url)
    get_settings.cache_clear()
