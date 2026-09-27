"""Column-grain lineage coverage + trace (#1710) — driven by the CAPTURED Unity Catalog payload.

The edges under test are produced by the real `UnityCatalogLineageProvider` reading the #901
live capture (`uc_table_lineage_projected.json` / `uc_column_lineage_projected.json`) and
persisted by the real `refresh_connection_lineage`, so the pair shapes are the warehouse's, not
ours. Only the TCP socket is faked.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import Asset, Connection, LineageEdge, User
from backend.app.lineage import columns as lc
from backend.app.lineage import warehouse_refresh
from backend.app.lineage.warehouse import ColumnGrain
from backend.app.lineage.warehouse_unity_catalog import UnityCatalogLineageProvider
from backend.app.services.asset_identity import format_unity_catalog_name
from backend.app.services.asset_service import upsert_assets
from backend.tests.lineage.test_warehouse_unity_catalog import _WORKSPACE, _FakeConn
from backend.tests.support.fake_secret_store import FakeSecretStore

_NS = "unitycatalog://adb-1234567890123456.4.azuredatabricks.net"


def _name(schema: str, table: str) -> str:
    return format_unity_catalog_name("dataq_retail", schema, table)


@pytest.fixture
def uc_connection(db_session: Session) -> Connection:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@x.io")
    db_session.add(user)
    db_session.flush()
    conn = Connection(
        name=f"uc-{uuid.uuid4().hex[:8]}",
        type="unity_catalog",
        env="dev",
        config={"workspace_url": _WORKSPACE, "warehouse_id": "w"},
        secret_ref="ref",
        created_by=user.id,
    )
    db_session.add(conn)
    db_session.flush()
    return conn


def _refresh(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
    connection: Connection,
    fake: _FakeConn | None = None,
) -> Any:
    @contextlib.contextmanager
    def _fake_open(_connection: Any, _secret_store: Any) -> Any:
        yield fake or _FakeConn()

    import backend.app.services.profile_service as profile_service

    monkeypatch.setattr(profile_service, "_open_connection", _fake_open)
    monkeypatch.setattr(
        warehouse_refresh,
        "get_warehouse_lineage_provider",
        lambda _t: UnityCatalogLineageProvider(),
    )
    return warehouse_refresh.refresh_connection_lineage(
        db_session, connection=connection, secret_store=FakeSecretStore()
    )


def _asset_id(db_session: Session, schema: str, table: str) -> uuid.UUID:
    return db_session.scalars(
        select(Asset.id).where(Asset.namespace == _NS, Asset.name == _name(schema, table))
    ).one()


@pytest.fixture
def captured(
    monkeypatch: pytest.MonkeyPatch, db_session: Session, uc_connection: Connection
) -> Connection:
    outcome = _refresh(monkeypatch, db_session, uc_connection)
    assert outcome is not None and outcome.column_grain is ColumnGrain.CAPTURED
    return uc_connection


# ── coverage vocabulary ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("source", "has_pairs", "grain", "expected"),
    [
        ("unity_catalog", True, None, lc.ColumnCoverage.RECORDED),
        ("dbt", True, None, lc.ColumnCoverage.RECORDED),
        ("dbt", False, "captured", lc.ColumnCoverage.NOT_CAPTURED),
        ("marquez", False, None, lc.ColumnCoverage.NOT_CAPTURED),
        ("snowflake", False, None, lc.ColumnCoverage.UNKNOWN),
        ("snowflake", False, "captured", lc.ColumnCoverage.NONE_RECORDED),
        ("unity_catalog", False, "unavailable", lc.ColumnCoverage.UNAVAILABLE),
        ("snowflake", False, "not_supported", lc.ColumnCoverage.NOT_CAPTURED),
        # A value this build does not know must not be promoted to a confident state.
        ("snowflake", False, "something_new", lc.ColumnCoverage.UNKNOWN),
    ],
)
def test_row_coverage(
    source: str, has_pairs: bool, grain: str | None, expected: lc.ColumnCoverage
) -> None:
    assert lc.row_coverage(source=source, has_pairs=has_pairs, column_grain=grain) is expected


def test_combine_takes_the_strongest_statement() -> None:
    c = lc.ColumnCoverage
    assert lc.combine([c.NOT_CAPTURED, c.RECORDED]) is c.RECORDED
    assert lc.combine([c.NOT_CAPTURED, c.UNKNOWN]) is c.UNKNOWN
    assert lc.combine([c.UNAVAILABLE, c.NONE_RECORDED]) is c.NONE_RECORDED
    assert lc.combine([c.UNKNOWN, c.UNAVAILABLE]) is c.UNAVAILABLE
    assert lc.combine([]) is c.NOT_CAPTURED


# ── the captured chain ──────────────────────────────────────────────────────


def test_refresh_records_captured_grain_on_the_connection(
    db_session: Session, captured: Connection
) -> None:
    assert captured.lineage_column_grain == "captured"


def test_upstream_trace_reaches_the_raw_origin(db_session: Session, captured: Connection) -> None:
    gold = _asset_id(db_session, "gold", "feedback_sentiment")
    trace = lc.trace_column(db_session, gold, "customer_id", direction=lc.TraceDirection.UPSTREAM)
    raw = _asset_id(db_session, "raw", "feedback")
    silver = _asset_id(db_session, "silver", "feedback")
    assert trace.upstream_status is lc.TraceStatus.TRACED
    assert trace.downstream_status is None
    assert [(n.asset_id, n.column, n.depth) for n in trace.upstream] == [
        (silver, "customer_id", 1),
        (raw, "customer_id", 2),
    ]
    assert [(o.asset_id, o.column, o.confirmed) for o in trace.origins] == [
        (raw, "customer_id", True)
    ]
    assert trace.complete


def test_trace_follows_a_renamed_derivation(db_session: Session, captured: Connection) -> None:
    """`sentiment` is derived from `comment` — a pair records derivation, not name equality."""
    gold = _asset_id(db_session, "gold", "feedback_sentiment")
    trace = lc.trace_column(db_session, gold, "sentiment", direction=lc.TraceDirection.UPSTREAM)
    assert [(n.column, n.depth) for n in trace.upstream] == [("comment", 1), ("comment", 2)]


def test_downstream_trace_stops_where_the_column_is_dropped(
    db_session: Session, captured: Connection
) -> None:
    """raw.feedback.email reaches silver but gold never selects it."""
    raw = _asset_id(db_session, "raw", "feedback")
    silver = _asset_id(db_session, "silver", "feedback")
    trace = lc.trace_column(db_session, raw, "email", direction=lc.TraceDirection.DOWNSTREAM)
    assert [(n.asset_id, n.column) for n in trace.downstream] == [(silver, "email")]
    assert trace.downstream_status is lc.TraceStatus.TRACED
    assert trace.complete


def test_column_match_uses_the_engine_fold(db_session: Session, captured: Connection) -> None:
    gold = _asset_id(db_session, "gold", "feedback_sentiment")
    lower = lc.trace_column(db_session, gold, "customer_id")
    upper = lc.trace_column(db_session, gold, "  CUSTOMER_ID ")
    assert [n.asset_id for n in upper.upstream] == [n.asset_id for n in lower.upstream]
    assert upper.upstream


def test_unknown_column_is_none_recorded_not_unrelated(
    db_session: Session, captured: Connection
) -> None:
    gold = _asset_id(db_session, "gold", "feedback_sentiment")
    trace = lc.trace_column(db_session, gold, "no_such_column")
    assert trace.upstream == [] and trace.hops == [] and trace.origins == []
    assert trace.upstream_status is lc.TraceStatus.NONE_RECORDED
    # Downstream of a leaf mart: no table edges at all.
    assert trace.downstream_status is lc.TraceStatus.NO_TABLE_LINEAGE


def test_depth_cap_marks_the_trace_truncated(db_session: Session, captured: Connection) -> None:
    gold = _asset_id(db_session, "gold", "feedback_sentiment")
    trace = lc.trace_column(
        db_session, gold, "customer_id", direction=lc.TraceDirection.UPSTREAM, max_depth=1
    )
    assert trace.truncated and not trace.complete
    (origin,) = trace.origins
    assert origin.depth == 1 and origin.confirmed is False


def test_a_non_column_source_edge_is_a_gap(db_session: Session, captured: Connection) -> None:
    """A dbt edge into raw.feedback carries no columns: the origin can no longer be confirmed."""
    raw = _asset_id(db_session, "raw", "feedback")
    ids = upsert_assets(
        db_session, [{"namespace": _NS, "name": _name("landing", "feedback_files")}]
    )
    landing = ids[(_NS, _name("landing", "feedback_files"))]
    db_session.add(
        LineageEdge(
            upstream_asset_id=landing,
            downstream_asset_id=raw,
            source="dbt",
            connection_id=captured.id,
        )
    )
    db_session.flush()
    gold = _asset_id(db_session, "gold", "feedback_sentiment")
    trace = lc.trace_column(db_session, gold, "customer_id", direction=lc.TraceDirection.UPSTREAM)
    assert [(g.upstream_asset_id, g.coverage) for g in trace.gaps] == [
        (landing, lc.ColumnCoverage.NOT_CAPTURED)
    ]
    assert not trace.complete
    (origin,) = trace.origins
    assert origin.asset_id == raw and origin.confirmed is False


def test_failed_column_read_reads_unavailable(
    monkeypatch: pytest.MonkeyPatch, db_session: Session, uc_connection: Connection
) -> None:
    outcome = _refresh(
        monkeypatch,
        db_session,
        uc_connection,
        _FakeConn(column_raises=RuntimeError("PERMISSION_DENIED on column_lineage")),
    )
    assert outcome is not None
    assert uc_connection.lineage_column_grain == "unavailable"
    gold = _asset_id(db_session, "gold", "feedback_sentiment")
    trace = lc.trace_column(db_session, gold, "customer_id", direction=lc.TraceDirection.UPSTREAM)
    assert trace.upstream_status is lc.TraceStatus.INCOMPLETE
    assert {g.coverage for g in trace.gaps} == {lc.ColumnCoverage.UNAVAILABLE}


def test_grain_never_recorded_reads_unknown(
    monkeypatch: pytest.MonkeyPatch, db_session: Session, captured: Connection
) -> None:
    """A connection that has not refreshed since the column shipped must not claim a state."""
    for edge in db_session.scalars(
        select(LineageEdge).where(LineageEdge.connection_id == captured.id)
    ):
        edge.columns = None
    captured.lineage_column_grain = None
    db_session.flush()
    gold = _asset_id(db_session, "gold", "feedback_sentiment")
    refs = lc.edge_columns(db_session, [(_asset_id(db_session, "silver", "feedback"), gold)])
    (info,) = refs.values()
    assert info.pairs == () and info.coverage is lc.ColumnCoverage.UNKNOWN


def test_incremental_pull_with_no_new_events_keeps_the_recorded_grain(
    monkeypatch: pytest.MonkeyPatch, db_session: Session, captured: Connection
) -> None:
    """The watermark now excludes every captured row → no table edges → no column read. The
    connection keeps "captured" rather than being reset to unknown."""
    captured.lineage_watermark = datetime(2100, 1, 1, tzinfo=UTC)
    outcome = _refresh(monkeypatch, db_session, captured)
    assert outcome is not None and outcome.column_grain is None
    assert captured.lineage_column_grain == "captured"


def test_isolated_asset_has_no_table_lineage(db_session: Session, captured: Connection) -> None:
    ids = upsert_assets(db_session, [{"namespace": _NS, "name": _name("x", "island")}])
    trace = lc.trace_column(db_session, ids[(_NS, _name("x", "island"))], "id")
    assert trace.upstream_status is lc.TraceStatus.NO_TABLE_LINEAGE
    assert trace.downstream_status is lc.TraceStatus.NO_TABLE_LINEAGE
    # The traced column is never reported as its own (unverified) origin.
    assert trace.origins == []


# ── walk mechanics the captured payload can't express ───────────────────────


def _edge(
    db_session: Session,
    up: uuid.UUID,
    down: uuid.UUID,
    columns: Any,
    *,
    connection: Connection,
    source: str = "snowflake",
) -> None:
    db_session.add(
        LineageEdge(
            upstream_asset_id=up,
            downstream_asset_id=down,
            source=source,
            connection_id=connection.id,
            columns=columns,
        )
    )
    db_session.flush()


@pytest.fixture
def sf(db_session: Session, uc_connection: Connection) -> tuple[Connection, dict[str, uuid.UUID]]:
    uc_connection.type = "snowflake"
    uc_connection.lineage_column_grain = "captured"
    ns = "snowflake://ACCT"
    names = ["DB.S.A", "DB.S.B", "DB.S.C"]
    ids = upsert_assets(db_session, [{"namespace": ns, "name": n} for n in names])
    return uc_connection, {n.rsplit(".", 1)[1]: ids[(ns, n)] for n in names}


def test_snowflake_fold_matches_lowercase_input(
    db_session: Session, sf: tuple[Connection, dict[str, uuid.UUID]]
) -> None:
    conn, a = sf
    _edge(db_session, a["A"], a["B"], [["ORDER_ID", "ORDER_ID"]], connection=conn)
    trace = lc.trace_column(db_session, a["B"], "order_id")
    assert [(n.asset_id, n.column) for n in trace.upstream] == [(a["A"], "ORDER_ID")]


def test_a_column_cycle_terminates(
    db_session: Session, sf: tuple[Connection, dict[str, uuid.UUID]]
) -> None:
    conn, a = sf
    _edge(db_session, a["A"], a["B"], [["X", "X"]], connection=conn)
    _edge(db_session, a["B"], a["A"], [["X", "X"]], connection=conn)
    trace = lc.trace_column(db_session, a["A"], "X", max_depth=25)
    assert [n.asset_id for n in trace.upstream] == [a["B"]]
    assert [n.asset_id for n in trace.downstream] == [a["B"]]
    assert not trace.truncated


def test_fan_in_both_parents_are_origins(
    db_session: Session, sf: tuple[Connection, dict[str, uuid.UUID]]
) -> None:
    conn, a = sf
    _edge(db_session, a["A"], a["C"], [["P", "TOTAL"]], connection=conn)
    _edge(db_session, a["B"], a["C"], [["Q", "TOTAL"], ["R", "OTHER"]], connection=conn)
    trace = lc.trace_column(db_session, a["C"], "TOTAL", direction=lc.TraceDirection.UPSTREAM)
    assert sorted((o.asset_id, o.column) for o in trace.origins) == sorted(
        [(a["A"], "P"), (a["B"], "Q")]
    )
    assert all(o.confirmed for o in trace.origins)


def test_malformed_and_json_null_columns_degrade_to_no_pairs(
    db_session: Session, sf: tuple[Connection, dict[str, uuid.UUID]]
) -> None:
    conn, a = sf
    _edge(db_session, a["A"], a["B"], {"not": "a list"}, connection=conn)
    _edge(db_session, a["B"], a["C"], [["X"], ["X", "X"], "junk"], connection=conn)
    refs = lc.edge_columns(db_session, [(a["A"], a["B"]), (a["B"], a["C"])])
    assert refs[(a["A"], a["B"])].pairs == ()
    assert refs[(a["A"], a["B"])].coverage is lc.ColumnCoverage.NONE_RECORDED
    assert refs[(a["B"], a["C"])].pairs == (("X", "X"),)


def test_node_cap_truncates(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
    sf: tuple[Connection, dict[str, uuid.UUID]],
) -> None:
    conn, a = sf
    _edge(db_session, a["A"], a["B"], [["X", "X"], ["Y", "X"], ["Z", "X"]], connection=conn)
    monkeypatch.setattr(lc, "MAX_TRACE_NODES", 2)
    trace = lc.trace_column(db_session, a["B"], "X", direction=lc.TraceDirection.UPSTREAM)
    assert len(trace.upstream) == 2
    assert trace.truncated and not trace.complete
