"""warehouse-native lineage refresh tests (#858) — against the real test DB.

Covers the connection-scoped upsert/prune, the never-prune-on-unavailable guard, the
provenance isolation from dbt/marquez rows, and the empty-but-successful prune.
"""

from __future__ import annotations

import uuid
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

    source = "snowflake"

    def __init__(self, result: WarehouseLineageResult | Exception) -> None:
        self._result = result

    def fetch_edges(self, conn: object, *, connection_config: dict[str, object]) -> Any:
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
