"""warehouse-native lineage refresh tests (#858) — against the real test DB.

Covers the connection-scoped upsert/prune, the never-prune-on-unavailable guard, the
provenance isolation from dbt/marquez rows, and the empty-but-successful prune.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import Asset, Connection, LineageEdge, User
from backend.app.lineage.warehouse import (
    LineageEdgePair,
    LineageTier,
    WarehouseLineageResult,
    WarehouseLineageUnavailableError,
)
from backend.app.lineage.warehouse_refresh import refresh_warehouse_edges
from backend.app.services.asset_identity import AssetIdentity

_NS = "snowflake://ACCT"


def _ident(name: str) -> AssetIdentity:
    return AssetIdentity(namespace=_NS, name=f"DATAQ_DB.ANALYTICS.{name}")


class _StubProvider:
    """A WarehouseLineageProvider that returns a canned result (or raises)."""

    def __init__(
        self,
        result: WarehouseLineageResult | Exception,
        *,
        source: str = "snowflake",
        is_incremental: bool = False,
    ) -> None:
        self._result = result
        self.source = source
        self.is_incremental = is_incremental
        self.since_seen: Any = "unset"

    def fetch_edges(
        self, conn: object, *, connection_config: dict[str, object], since: Any = None
    ) -> Any:
        self.since_seen = since
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


@pytest.fixture
def sf_connection(db_session: Session) -> Connection:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@x.io")
    db_session.add(user)
    db_session.flush()
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "ACCT"},
        secret_ref="ref",
        created_by=user.id,
    )
    db_session.add(conn)
    db_session.flush()
    return conn


def _result(
    *pairs: tuple[str, str],
    tier: LineageTier = LineageTier.SNOWFLAKE_OBJECT_DEPENDENCIES,
    degraded: str | None = "view-level only",
) -> WarehouseLineageResult:
    return WarehouseLineageResult(
        edges=tuple(LineageEdgePair(_ident(u), _ident(d)) for u, d in pairs),
        tier=tier,
        degraded_reason=degraded,
    )


def _edges_for(
    session: Session, connection: Connection, source: str = "snowflake"
) -> set[tuple[str, str]]:
    """The (upstream_name, downstream_name) pairs cached for one (source, connection)."""
    name_by_id = {a.id: a.name for a in session.execute(select(Asset)).scalars()}
    return {
        (name_by_id[edge.upstream_asset_id], name_by_id[edge.downstream_asset_id])
        for edge in session.execute(
            select(LineageEdge).where(
                LineageEdge.source == source, LineageEdge.connection_id == connection.id
            )
        ).scalars()
    }


def test_refresh_materializes_assets_and_edges(
    sf_connection: Connection, db_session: Session
) -> None:
    provider = _StubProvider(_result(("STG_ORDERS", "MART_ORDERS"), ("STG_LINES", "MART_ORDERS")))
    outcome = refresh_warehouse_edges(
        db_session, connection=sf_connection, provider=provider, conn=object()
    )
    assert outcome is not None
    assert outcome.live_edges == 2
    assert outcome.tier == LineageTier.SNOWFLAKE_OBJECT_DEPENDENCIES
    assert outcome.degraded_reason == "view-level only"
    edges = _edges_for(db_session, sf_connection)
    assert (
        "DATAQ_DB.ANALYTICS.STG_ORDERS",
        "DATAQ_DB.ANALYTICS.MART_ORDERS",
    ) in edges


def test_refresh_prunes_edges_no_longer_seen(
    sf_connection: Connection, db_session: Session
) -> None:
    refresh_warehouse_edges(
        db_session,
        connection=sf_connection,
        provider=_StubProvider(_result(("A", "B"), ("C", "D"))),
        conn=object(),
    )
    # Second refresh drops (C,D), keeps (A,B), adds (E,F).
    outcome = refresh_warehouse_edges(
        db_session,
        connection=sf_connection,
        provider=_StubProvider(_result(("A", "B"), ("E", "F"))),
        conn=object(),
    )
    assert outcome is not None and outcome.live_edges == 2
    edges = _edges_for(db_session, sf_connection)
    names = {(u.split(".")[-1], d.split(".")[-1]) for u, d in edges}
    assert names == {("A", "B"), ("E", "F")}


def test_unavailable_never_prunes(sf_connection: Connection, db_session: Session) -> None:
    refresh_warehouse_edges(
        db_session,
        connection=sf_connection,
        provider=_StubProvider(_result(("A", "B"))),
        conn=object(),
    )
    before = _edges_for(db_session, sf_connection)
    assert before  # seeded

    # A subsequent UNAVAILABLE pull must leave the cache untouched (never wipe on outage).
    outcome = refresh_warehouse_edges(
        db_session,
        connection=sf_connection,
        provider=_StubProvider(WarehouseLineageUnavailableError("warehouse down")),
        conn=object(),
    )
    assert outcome is None
    assert _edges_for(db_session, sf_connection) == before  # unchanged


def test_empty_successful_pull_prunes_to_zero(
    sf_connection: Connection, db_session: Session
) -> None:
    refresh_warehouse_edges(
        db_session,
        connection=sf_connection,
        provider=_StubProvider(_result(("A", "B"))),
        conn=object(),
    )
    assert _edges_for(db_session, sf_connection)
    # A successful pull that found NOTHING is a true observation → prune to zero.
    outcome = refresh_warehouse_edges(
        db_session,
        connection=sf_connection,
        provider=_StubProvider(
            WarehouseLineageResult.empty(LineageTier.SNOWFLAKE_OBJECT_DEPENDENCIES)
        ),
        conn=object(),
    )
    assert outcome is not None and outcome.live_edges == 0
    assert _edges_for(db_session, sf_connection) == set()


def test_incremental_source_never_prunes(sf_connection: Connection, db_session: Session) -> None:
    # A log source (UC) is incremental: an edge from an earlier window must SURVIVE a
    # later refresh that didn't re-observe it — pruning it would erase real lineage.
    def uc(*pairs: tuple[str, str]) -> _StubProvider:
        return _StubProvider(
            _result(*pairs, tier=LineageTier.UNITY_CATALOG_SYSTEM_ACCESS, degraded=None),
            source="unity_catalog",
            is_incremental=True,
        )

    refresh_warehouse_edges(
        db_session, connection=sf_connection, provider=uc(("A", "B")), conn=object()
    )
    # A second refresh observing a DIFFERENT edge must ADD it, keeping the first.
    outcome = refresh_warehouse_edges(
        db_session,
        connection=sf_connection,
        provider=uc(("C", "D")),
        conn=object(),
        since=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert outcome is not None and outcome.live_edges == 2  # both kept (no prune)
    names = {
        (u.split(".")[-1], d.split(".")[-1])
        for u, d in _edges_for(db_session, sf_connection, source="unity_catalog")
    }
    assert names == {("A", "B"), ("C", "D")}


def test_since_watermark_threaded_to_provider(
    sf_connection: Connection, db_session: Session
) -> None:
    provider = _StubProvider(
        _result(("A", "B"), tier=LineageTier.UNITY_CATALOG_SYSTEM_ACCESS, degraded=None),
        source="unity_catalog",
        is_incremental=True,
    )
    mark = datetime(2026, 5, 1, tzinfo=UTC)
    refresh_warehouse_edges(
        db_session, connection=sf_connection, provider=provider, conn=object(), since=mark
    )
    assert provider.since_seen == mark  # the persisted watermark reached the provider


def test_prune_is_scoped_to_source_and_connection(
    sf_connection: Connection, db_session: Session
) -> None:
    # A dbt edge on the SAME connection must survive a snowflake-source refresh — the
    # prune keys on (source, connection_id), never an endpoint-set heuristic.
    from sqlalchemy import func

    a = Asset(namespace=_NS, name="DATAQ_DB.ANALYTICS.X", env="dev")
    b = Asset(namespace=_NS, name="DATAQ_DB.ANALYTICS.Y", env="dev")
    db_session.add_all([a, b])
    db_session.flush()
    db_session.add(
        LineageEdge(
            upstream_asset_id=a.id,
            downstream_asset_id=b.id,
            source="dbt",
            connection_id=sf_connection.id,
            last_seen=func.clock_timestamp(),
        )
    )
    db_session.flush()

    refresh_warehouse_edges(
        db_session,
        connection=sf_connection,
        provider=_StubProvider(_result(("A", "B"))),
        conn=object(),
    )
    # the dbt edge is untouched by the snowflake prune
    assert _edges_for(db_session, sf_connection, source="dbt") == {
        ("DATAQ_DB.ANALYTICS.X", "DATAQ_DB.ANALYTICS.Y")
    }
    assert _edges_for(db_session, sf_connection, source="snowflake")


def test_get_warehouse_lineage_provider_registry() -> None:
    from backend.app.lineage.warehouse import get_warehouse_lineage_provider

    sf = get_warehouse_lineage_provider("snowflake")
    assert sf is not None and sf.source == "snowflake" and sf.is_incremental is False
    uc = get_warehouse_lineage_provider("unity_catalog")
    assert uc is not None and uc.source == "unity_catalog" and uc.is_incremental is True
    # a type with no warehouse-native lineage (dbt/OpenLineage feeds it instead)
    assert get_warehouse_lineage_provider("adls_gen2") is None
    assert get_warehouse_lineage_provider("iceberg") is None


# ── column grain persistence (#901) ───────────────────────────────────────────


def _columns_for(
    session: Session, connection: Connection, source: str = "snowflake"
) -> dict[tuple[str, str], Any]:
    name_by_id = {a.id: a.name for a in session.execute(select(Asset)).scalars()}
    return {
        (name_by_id[e.upstream_asset_id], name_by_id[e.downstream_asset_id]): e.columns
        for e in session.execute(
            select(LineageEdge).where(
                LineageEdge.source == source, LineageEdge.connection_id == connection.id
            )
        ).scalars()
    }


def test_column_pairs_persist_on_the_edge(db_session: Session, sf_connection: Connection) -> None:
    result = WarehouseLineageResult(
        edges=(
            LineageEdgePair(_ident("SRC"), _ident("DST"), column_pairs=(("a", "b"), ("c", "d"))),
            LineageEdgePair(_ident("SRC"), _ident("OTHER")),
        ),
        tier=LineageTier.SNOWFLAKE_OBJECT_DEPENDENCIES,
    )
    refresh_warehouse_edges(
        db_session, connection=sf_connection, provider=_StubProvider(result), conn=object()
    )
    cols = _columns_for(db_session, sf_connection)
    assert cols[(_ident("SRC").name, _ident("DST").name)] == [["a", "b"], ["c", "d"]]
    # An edge with no observed pairs stays NULL — "never observed" is not "zero pairs".
    assert cols[(_ident("SRC").name, _ident("OTHER").name)] is None


def test_incremental_refresh_merges_column_pairs_never_forgets(
    db_session: Session, sf_connection: Connection
) -> None:
    """A log window only re-observes pairs whose queries ran inside it — the union
    with the persisted pairs is what keeps the never-prune promise at column grain."""

    def _incremental(pairs: tuple[tuple[str, str], ...]) -> _StubProvider:
        return _StubProvider(
            WarehouseLineageResult(
                edges=(LineageEdgePair(_ident("SRC"), _ident("DST"), column_pairs=pairs),),
                tier=LineageTier.UNITY_CATALOG_SYSTEM_ACCESS,
            ),
            source="unity_catalog",
            is_incremental=True,
        )

    refresh_warehouse_edges(
        db_session,
        connection=sf_connection,
        provider=_incremental((("a", "b"), ("c", "d"))),
        conn=object(),
    )
    # Second window observes ONE old pair and one new — the union must keep all three.
    refresh_warehouse_edges(
        db_session,
        connection=sf_connection,
        provider=_incremental((("c", "d"), ("e", "f"))),
        conn=object(),
    )
    cols = _columns_for(db_session, sf_connection, source="unity_catalog")
    assert cols[(_ident("SRC").name, _ident("DST").name)] == [
        ["a", "b"],
        ["c", "d"],
        ["e", "f"],
    ]
    # And a later window with NO column events must not regress the pairs to NULL.
    refresh_warehouse_edges(
        db_session, connection=sf_connection, provider=_incremental(()), conn=object()
    )
    cols = _columns_for(db_session, sf_connection, source="unity_catalog")
    assert cols[(_ident("SRC").name, _ident("DST").name)] == [
        ["a", "b"],
        ["c", "d"],
        ["e", "f"],
    ]


def test_bulk_upsert_no_pairs_edge_stores_sql_null_not_json_null(
    db_session: Session, sf_connection: Connection
) -> None:
    """#907 pinned at the ACTUAL defect path — the multi-VALUES bulk upsert (the
    writer that produced prod's 339 JSON-null rows). ORM reads can't tell the two
    nulls apart (both deserialize to Python None), so assert in SQL."""
    from sqlalchemy import text as sql_text

    refresh_warehouse_edges(
        db_session,
        connection=sf_connection,
        provider=_StubProvider(
            WarehouseLineageResult(
                edges=(LineageEdgePair(_ident("SRC"), _ident("DST")),),
                tier=LineageTier.SNOWFLAKE_OBJECT_DEPENDENCIES,
            )
        ),
        conn=object(),
    )
    json_null = db_session.execute(
        sql_text(
            "SELECT count(*) FROM lineage_edges "
            "WHERE connection_id = :c AND columns = 'null'::jsonb"
        ),
        {"c": str(sf_connection.id)},
    ).scalar_one()
    sql_null = db_session.execute(
        sql_text("SELECT count(*) FROM lineage_edges WHERE connection_id = :c AND columns IS NULL"),
        {"c": str(sf_connection.id)},
    ).scalar_one()
    assert json_null == 0
    assert sql_null == 1
