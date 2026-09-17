"""warehouse-native lineage refresh tests (#858) — against the real test DB."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.db.models import Asset, Connection, LineageEdge, User
from backend.app.lineage.warehouse import (
    MAX_COLUMN_PAIRS_PER_EDGE,
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

    def enumerate_tables(
        self, conn: object, *, connection_config: dict[str, object], limit: int | None = None
    ) -> tuple[Any, ...]:
        return ()  # Protocol conformance (ADR 0040); the refresh tests never enumerate


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


def test_partial_pull_never_prunes_the_richer_cached_graph(
    sf_connection: Connection, db_session: Session
) -> None:
    """#1109 review — the regression the descend-don't-abort fix would otherwise have introduced. A
    transient blip inside the GET_LINEAGE tier makes the provider descend and return a real,
    successful FLOOR-tier result.
    """
    refresh_warehouse_edges(
        db_session,
        connection=sf_connection,
        provider=_StubProvider(
            _result(("A", "B"), ("C", "D"), tier=LineageTier.SNOWFLAKE_GET_LINEAGE, degraded=None)
        ),
        conn=object(),
    )
    assert _edges_for(db_session, sf_connection)

    partial = WarehouseLineageResult(
        edges=(LineageEdgePair(_ident("A"), _ident("B")),),
        tier=LineageTier.SNOWFLAKE_OBJECT_DEPENDENCIES,
        degraded_reason="view-level lineage only — get_lineage: call failed (RuntimeError)",
        skipped_tiers=(
            "get_lineage: call failed (RuntimeError) (transient — retried next refresh)",
        ),
        prunable=False,
    )
    outcome = refresh_warehouse_edges(
        db_session, connection=sf_connection, provider=_StubProvider(partial), conn=object()
    )
    assert outcome is not None and outcome.live_edges == 2  # (C,D) survived the blip
    names = {(u.split(".")[-1], d.split(".")[-1]) for u, d in _edges_for(db_session, sf_connection)}
    assert names == {("A", "B"), ("C", "D")}

    # …and the NEXT clean pull prunes normally, so a genuinely removed dependency is at worst one
    # cycle late — not frozen in the cache forever.
    outcome = refresh_warehouse_edges(
        db_session,
        connection=sf_connection,
        provider=_StubProvider(_result(("A", "B"))),
        conn=object(),
    )
    assert outcome is not None and outcome.live_edges == 1
    names = {(u.split(".")[-1], d.split(".")[-1]) for u, d in _edges_for(db_session, sf_connection)}
    assert names == {("A", "B")}


def test_a_confirmed_degraded_pull_still_prunes(
    sf_connection: Connection, db_session: Session
) -> None:
    """The counterpart: a tier skipped for a CONFIRMED reason (edition gate, missing
    grant) is a degraded pull that is nonetheless current truth — the account will
    answer identically next cycle. It must keep pruning, or a dependency dropped on a
    Standard-edition account would never leave the cache.
    """
    refresh_warehouse_edges(
        db_session,
        connection=sf_connection,
        provider=_StubProvider(_result(("A", "B"), ("C", "D"))),
        conn=object(),
    )
    gated = WarehouseLineageResult(
        edges=(LineageEdgePair(_ident("A"), _ident("B")),),
        tier=LineageTier.SNOWFLAKE_OBJECT_DEPENDENCIES,
        degraded_reason="view-level lineage only — get_lineage: unsupported on this edition",
        skipped_tiers=("get_lineage: unsupported on this edition",),
        # prunable defaults True — a confirmed skip is not a partial observation.
    )
    outcome = refresh_warehouse_edges(
        db_session, connection=sf_connection, provider=_StubProvider(gated), conn=object()
    )
    assert outcome is not None and outcome.live_edges == 1
    names = {(u.split(".")[-1], d.split(".")[-1]) for u, d in _edges_for(db_session, sf_connection)}
    assert names == {("A", "B")}


def test_partial_pull_does_not_wipe_column_pairs(
    sf_connection: Connection, db_session: Session
) -> None:
    """Gating only the PRUNE would have left the other destructive half armed (#1109 review): a
    snapshot pull replaces `columns` verbatim, and the floor tier carries no column pairs at all
    — so a cycle where GET_LINEAGE blipped would overwrite a real column mapping with NULL while
    leaving the edge itself in place.
    """
    rich = WarehouseLineageResult(
        edges=(LineageEdgePair(_ident("SRC"), _ident("DST"), column_pairs=(("a", "b"),)),),
        tier=LineageTier.SNOWFLAKE_GET_LINEAGE,
    )
    refresh_warehouse_edges(
        db_session, connection=sf_connection, provider=_StubProvider(rich), conn=object()
    )
    partial = WarehouseLineageResult(
        edges=(LineageEdgePair(_ident("SRC"), _ident("DST")),),  # floor grain — no pairs
        tier=LineageTier.SNOWFLAKE_OBJECT_DEPENDENCIES,
        prunable=False,
    )
    refresh_warehouse_edges(
        db_session, connection=sf_connection, provider=_StubProvider(partial), conn=object()
    )
    cols = _columns_for(db_session, sf_connection)
    assert cols[(_ident("SRC").name, _ident("DST").name)] == [["a", "b"]]


def test_the_accreting_column_union_is_capped(
    sf_connection: Connection, db_session: Session
) -> None:
    """#1109 review: each provider caps column pairs inside its own `_EdgeSet`, which bounds ONE
    pull — but the persisted union accretes ACROSS pulls, so an ETL reporting fresh pairs every
    cycle grew the edge's JSONB without bound.
    """

    def _window(start: int) -> _StubProvider:
        pairs = tuple((f"s{i}", f"d{i}") for i in range(start, start + MAX_COLUMN_PAIRS_PER_EDGE))
        return _StubProvider(
            WarehouseLineageResult(
                edges=(LineageEdgePair(_ident("SRC"), _ident("DST"), column_pairs=pairs),),
                tier=LineageTier.UNITY_CATALOG_SYSTEM_ACCESS,
            ),
            source="unity_catalog",
            is_incremental=True,
        )

    refresh_warehouse_edges(
        db_session, connection=sf_connection, provider=_window(0), conn=object()
    )
    # A second window of entirely NEW pairs would have doubled the blob uncapped.
    refresh_warehouse_edges(
        db_session,
        connection=sf_connection,
        provider=_window(MAX_COLUMN_PAIRS_PER_EDGE),
        conn=object(),
    )
    cols = _columns_for(db_session, sf_connection, source="unity_catalog")
    assert len(cols[(_ident("SRC").name, _ident("DST").name)]) == MAX_COLUMN_PAIRS_PER_EDGE


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
    with the persisted pairs is what keeps the never-prune promise at column grain.
    """

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
    nulls apart (both deserialize to Python None), so assert in SQL.
    """
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


def test_snapshot_refresh_replaces_column_pairs_never_accretes(
    db_session: Session, sf_connection: Connection
) -> None:
    """#911 review: for a snapshot source the pull IS the current truth — a pair the
    warehouse no longer reports (rewritten ETL) must go away, and a grain lost with a
    revoked grant must clear rather than freeze at revocation time.
    """

    def _snapshot(pairs: tuple[tuple[str, str], ...]) -> _StubProvider:
        return _StubProvider(
            WarehouseLineageResult(
                edges=(LineageEdgePair(_ident("SRC"), _ident("DST"), column_pairs=pairs),),
                tier=LineageTier.SNOWFLAKE_ACCESS_HISTORY,
            )
        )

    refresh_warehouse_edges(
        db_session, connection=sf_connection, provider=_snapshot((("a", "b"),)), conn=object()
    )
    refresh_warehouse_edges(
        db_session, connection=sf_connection, provider=_snapshot((("c", "d"),)), conn=object()
    )
    cols = _columns_for(db_session, sf_connection)
    assert cols[(_ident("SRC").name, _ident("DST").name)] == [["c", "d"]]  # replaced, not unioned
    # Grain lost entirely (revoked grant → floor-only pull, no pairs) → cleared.
    refresh_warehouse_edges(
        db_session, connection=sf_connection, provider=_snapshot(()), conn=object()
    )
    cols = _columns_for(db_session, sf_connection)
    assert cols[(_ident("SRC").name, _ident("DST").name)] is None


# ── #1236: the prune-suspension backstop ─────────────────────────────────────


def _partial(*names: tuple[str, str]) -> WarehouseLineageResult:
    """A snapshot pull that only PARTIALLY observed current state — no prune licence."""
    return WarehouseLineageResult(
        edges=tuple(LineageEdgePair(_ident(u), _ident(d)) for u, d in names),
        tier=LineageTier.SNOWFLAKE_OBJECT_DEPENDENCIES,
        degraded_reason="view-level lineage only — get_lineage: call failed (RuntimeError)",
        prunable=False,
    )


def _refresh(
    db_session: Session, connection: Connection, result: WarehouseLineageResult, **kwargs: Any
) -> Any:
    return refresh_warehouse_edges(
        db_session,
        connection=connection,
        provider=_StubProvider(result, **kwargs),
        conn=object(),
    )


def test_a_clean_pull_stamps_the_prune_marker(
    sf_connection: Connection, db_session: Session
) -> None:
    """The backstop's clock only exists because a pruning pull records itself."""
    assert sf_connection.lineage_last_authoritative_refresh_at is None
    outcome = _refresh(db_session, sf_connection, _result(("A", "B")))
    assert outcome is not None
    assert sf_connection.lineage_last_authoritative_refresh_at is not None
    assert outcome.prune_suspended is False
    assert outcome.prune_forced is False


def test_n_consecutive_partial_cycles_fire_the_backstop_exactly_once_past_the_threshold(
    sf_connection: Connection, db_session: Session
) -> None:
    """The defect #1236 names: a persistent condition that READS as transient suspends
    the prune every cycle, forever, and `lineage_edges` accretes with no bound.

    Drives five consecutive partial pulls. The first four are inside
    LINEAGE_STALE_AFTER_HOURS and must NOT prune — that is the accrete-only trade
    working. The fifth is past it and prunes anyway, and having re-stamped, the sixth
    goes back to suspending rather than pruning on every partial pull thereafter.
    """
    hours = get_settings().lineage_stale_after_hours
    _refresh(db_session, sf_connection, _result(("A", "B"), ("C", "D")))
    pruned_at = sf_connection.lineage_last_authoritative_refresh_at
    assert pruned_at is not None

    for cycle in range(4):
        outcome = _refresh(db_session, sf_connection, _partial(("A", "B")))
        assert outcome is not None, cycle
        assert outcome.prune_forced is False, cycle
        assert outcome.prune_suspended is True, cycle
        # (C,D) survives every suspended cycle — nothing has been wiped by a blip.
        assert outcome.live_edges == 2, cycle
        assert sf_connection.lineage_last_authoritative_refresh_at == pruned_at, cycle

    # Age the marker past the threshold: the suspension has now outlived its window.
    sf_connection.lineage_last_authoritative_refresh_at = datetime.now(UTC) - timedelta(
        hours=hours + 1
    )
    db_session.commit()

    outcome = _refresh(db_session, sf_connection, _partial(("A", "B")))
    assert outcome is not None
    assert outcome.prune_forced is True
    assert outcome.prune_suspended is False
    assert outcome.live_edges == 1  # (C,D) finally removed
    forced_at = sf_connection.lineage_last_authoritative_refresh_at
    assert forced_at is not None and forced_at > datetime.now(UTC) - timedelta(minutes=5)

    # And it re-stamped, so the NEXT partial pull suspends again rather than the backstop
    # degrading into "prune on every partial pull" (which would discard the whole trade).
    outcome = _refresh(db_session, sf_connection, _partial(("A", "B"), ("E", "F")))
    assert outcome is not None
    assert outcome.prune_forced is False
    assert outcome.prune_suspended is True
    assert sf_connection.lineage_last_authoritative_refresh_at == forced_at


def test_the_backstop_does_not_fire_on_a_first_ever_partial_pull(
    sf_connection: Connection, db_session: Session
) -> None:
    """A NULL marker means no prune has ever been recorded — there is no suspension age
    to exceed. Pruning here would delete edges accreted from earlier partial pulls
    against an observation never shown to be complete, so it is reported, not forced.
    """
    assert sf_connection.lineage_last_authoritative_refresh_at is None
    outcome = _refresh(db_session, sf_connection, _partial(("A", "B")))
    assert outcome is not None
    assert outcome.prune_forced is False
    assert outcome.prune_suspended is True
    # The honest half: the age is reported as None ("never"), not silently as "recent".
    assert outcome.prune_suspended_since is None
    assert sf_connection.lineage_last_authoritative_refresh_at is None


def test_an_incremental_source_never_reports_a_suspension(
    sf_connection: Connection, db_session: Session
) -> None:
    """An incremental (log) source prunes NOTHING by design, so "has not pruned" is not a
    fault — reporting it would put a permanent warning on a healthy Unity Catalog
    connection.
    """
    sf_connection.lineage_last_authoritative_refresh_at = datetime.now(UTC) - timedelta(days=365)
    db_session.commit()
    outcome = _refresh(
        db_session,
        sf_connection,
        _partial(("A", "B")),
        source="uc",
        is_incremental=True,
    )
    assert outcome is not None
    assert outcome.prune_suspended is False
    assert outcome.prune_forced is False


def test_a_forced_prune_does_not_replace_column_pairs(
    sf_connection: Connection, db_session: Session
) -> None:
    """Only the stale-edge half is forced. A partial pull's COLUMN set is genuinely
    partial, so replacing it verbatim would overwrite a real column mapping with the
    floor tier's nothing — exactly the second destructive half #1109 gated.
    """
    rich = WarehouseLineageResult(
        edges=(LineageEdgePair(_ident("SRC"), _ident("DST"), column_pairs=(("a", "b"),)),),
        tier=LineageTier.SNOWFLAKE_GET_LINEAGE,
    )
    _refresh(db_session, sf_connection, rich)
    sf_connection.lineage_last_authoritative_refresh_at = datetime.now(UTC) - timedelta(days=365)
    db_session.commit()

    outcome = _refresh(db_session, sf_connection, _partial(("SRC", "DST")))
    assert outcome is not None and outcome.prune_forced is True
    cols = _columns_for(db_session, sf_connection)
    assert cols[(_ident("SRC").name, _ident("DST").name)] == [["a", "b"]]


def test_a_non_positive_threshold_disables_the_backstop(
    sf_connection: Connection, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`LINEAGE_STALE_AFTER_HOURS <= 0` already disables the staleness signal; the
    backstop reads the same setting and must honour the same off switch, or turning the
    signal off would silently arm a destructive behaviour instead.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "lineage_stale_after_hours", 0)
    _refresh(db_session, sf_connection, _result(("A", "B"), ("C", "D")))
    sf_connection.lineage_last_authoritative_refresh_at = datetime.now(UTC) - timedelta(days=365)
    db_session.commit()

    outcome = _refresh(db_session, sf_connection, _partial(("A", "B")))
    assert outcome is not None
    assert outcome.prune_forced is False
    assert outcome.live_edges == 2


def test_the_backstop_does_not_fire_on_a_pull_that_observed_nothing(
    sf_connection: Connection, db_session: Session
) -> None:
    """A floor on the forced prune, mirroring the `if identities:` guard on the upsert half.

    Snowflake's "floor tier unavailable" branch can return a partial result with NO edges
    at all. Forcing a prune against it deletes the source's ENTIRE graph on the strength of
    a pull that admits it learned nothing — the opposite of the accrete-only trade. An
    empty pull that IS prunable is a true observation and still prunes; only the forced
    prune is withheld.
    """
    _refresh(db_session, sf_connection, _result(("A", "B"), ("C", "D")))
    sf_connection.lineage_last_authoritative_refresh_at = datetime.now(UTC) - timedelta(days=365)
    db_session.commit()

    outcome = _refresh(db_session, sf_connection, _partial())  # no edges observed
    assert outcome is not None
    assert outcome.prune_forced is False
    assert outcome.prune_suspended is True
    assert outcome.live_edges == 2, "the whole graph was wiped by a pull that saw nothing"


def test_a_genuinely_empty_but_prunable_pull_still_prunes(
    sf_connection: Connection, db_session: Session
) -> None:
    """The counterpart the floor must not break: `WarehouseLineageResult.empty()` is a pull
    that ran and observed no edges — a true current-state observation. It prunes, or a
    dependency removed from a warehouse that now has none would live in the cache forever.
    """
    _refresh(db_session, sf_connection, _result(("A", "B")))
    outcome = _refresh(
        db_session,
        sf_connection,
        WarehouseLineageResult.empty(LineageTier.SNOWFLAKE_GET_LINEAGE),
    )
    assert outcome is not None
    assert outcome.prune_suspended is False
    assert outcome.live_edges == 0
