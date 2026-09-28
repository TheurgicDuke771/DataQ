"""#1710: column grain on the Snowflake GET_LINEAGE tier (ACCESS_HISTORY refinement).

The GET_LINEAGE rows are the real capture (all table-grain: NULL column names at TABLE domain —
re-confirmed live 2026-09-27, which is WHY this refinement exists); the ACCESS_HISTORY `columns`
blob shape is the #908 live-tuned one. Live (2026-09-27, DATAQ_READER): the refinement ran on the
GET_LINEAGE tier and stamped `captured`; the account has no DML with non-empty `directSources`, so
the pair-ATTACH path is exercised only here (creating qualifying DML would be a live write).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from backend.app.db.models import Asset, Connection, LineageEdge, User
from backend.app.lineage.warehouse import ColumnGrain, LineageTier
from backend.app.lineage.warehouse_refresh import refresh_warehouse_edges
from backend.app.lineage.warehouse_snowflake import SnowflakeLineageProvider
from backend.tests.lineage.test_warehouse_snowflake import (
    _CONFIG,
    _FakeConn,
    _feature_unsupported_error,
    _get_lineage_rows,
    _GetLineageConn,
    _object_dependencies_rows,
    _sf,
)


def _gl_orders_conn(**kwargs: Any) -> _GetLineageConn:
    return _GetLineageConn(
        {
            ("DATAQ_DB.RETAIL.ORDERS_HEADER", "DOWNSTREAM"): _get_lineage_rows(
                "gl_down_orders_header"
            )
        },
        **kwargs,
    )


def _ah_row(source: str, target: str, pairs: list[tuple[str, str]]) -> tuple[str, str, str]:
    blob = json.dumps(
        [
            {
                "columnName": written,
                "directSources": [
                    {"columnName": read, "objectDomain": "Table", "objectName": source}
                ],
            }
            for read, written in pairs
        ]
    )
    return (source, target, blob)


def test_get_lineage_alone_records_no_column_pairs() -> None:
    """The premise: every real GET_LINEAGE row is table-grain. The refinement read ran (and
    found nothing), so the absence is a true observation — CAPTURED, not unknown."""
    conn = _gl_orders_conn()
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    assert result.tier == LineageTier.SNOWFLAKE_GET_LINEAGE
    assert result.edges and all(e.column_pairs == () for e in result.edges)
    assert result.column_grain is ColumnGrain.CAPTURED
    assert any("ACCESS_HISTORY ah" in sql for sql in conn.executed)


def test_get_lineage_edges_are_refined_with_access_history_pairs() -> None:
    conn = _gl_orders_conn(
        results={
            "ACCESS_HISTORY ah": [
                _ah_row(
                    "DATAQ_DB.RETAIL.ORDERS_HEADER",
                    "DATAQ_DB.ANALYTICS_STG.STG_ORDERS",
                    [("CUSTOMER_ID", "CUSTOMER_ID"), ("SUBTOTAL", "ORDER_TOTAL")],
                ),
                # A DML-only edge GET_LINEAGE did not return — must NOT become a table edge.
                _ah_row("DATAQ_DB.RETAIL.OTHER", "DATAQ_DB.RETAIL.ELSEWHERE", [("A", "A")]),
            ]
        }
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    by_edge = {(e.upstream.name, e.downstream.name): e.column_pairs for e in result.edges}
    assert by_edge[(_sf("RETAIL", "ORDERS_HEADER"), _sf("ANALYTICS_STG", "STG_ORDERS"))] == (
        ("CUSTOMER_ID", "CUSTOMER_ID"),
        ("SUBTOTAL", "ORDER_TOTAL"),
    )
    assert (_sf("RETAIL", "OTHER"), _sf("RETAIL", "ELSEWHERE")) not in by_edge
    assert len(by_edge) == 3
    assert result.column_grain is ColumnGrain.CAPTURED
    assert result.degraded_reason is None
    assert result.prunable is True


def test_a_failed_column_refinement_keeps_the_table_edges_and_says_so() -> None:
    conn = _gl_orders_conn(raises={"ACCESS_HISTORY": RuntimeError("raw driver text")})
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    assert result.tier == LineageTier.SNOWFLAKE_GET_LINEAGE
    assert len(result.edges) == 3
    assert result.column_grain is ColumnGrain.UNAVAILABLE
    assert result.degraded_reason is not None
    assert "column detail unavailable — access_history" in result.degraded_reason
    assert "raw driver text" not in result.degraded_reason  # never raw text (#902)
    # A column-read failure is not a table-edge observation failure…
    assert result.prunable is True
    # …but a transient one must not license replacing stored pairs.
    assert result.columns_authoritative is False
    assert "transient" in result.degraded_reason


def test_the_floor_failure_branch_is_refined_too() -> None:
    conn = _gl_orders_conn(
        results={
            "ACCESS_HISTORY ah": [
                _ah_row(
                    "DATAQ_DB.RETAIL.ORDERS_HEADER",
                    "DATAQ_DB.ANALYTICS_STG.STG_ORDERS",
                    [("CUSTOMER_ID", "CUSTOMER_ID")],
                )
            ]
        },
        raises={"OBJECT_DEPENDENCIES": RuntimeError("floor blip")},
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    assert result.degraded_reason is not None
    assert result.degraded_reason.startswith("floor unavailable")
    assert result.column_grain is ColumnGrain.CAPTURED
    assert any(e.column_pairs for e in result.edges)


def test_a_failed_refinement_on_the_floor_failure_branch_notes_both() -> None:
    conn = _gl_orders_conn(
        raises={
            "OBJECT_DEPENDENCIES": RuntimeError("floor blip"),
            "ACCESS_HISTORY": RuntimeError("ah blip"),
        },
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    assert result.column_grain is ColumnGrain.UNAVAILABLE
    assert result.degraded_reason is not None
    assert "object_dependencies" in result.degraded_reason
    assert "column detail unavailable" in result.degraded_reason


def test_access_history_tier_failure_reports_unavailable_grain() -> None:
    conn = _FakeConn(
        results={"OBJECT_DEPENDENCIES": _object_dependencies_rows()},
        raises={
            "GET_LINEAGE": _feature_unsupported_error(),
            "ACCESS_HISTORY": _feature_unsupported_error(),
        },
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    assert result.tier == LineageTier.SNOWFLAKE_OBJECT_DEPENDENCIES
    assert result.column_grain is ColumnGrain.UNAVAILABLE


def test_access_history_tier_that_read_nothing_still_captured_grain() -> None:
    conn = _FakeConn(
        results={"OBJECT_DEPENDENCIES": _object_dependencies_rows()},
        raises={"GET_LINEAGE": _feature_unsupported_error()},
    )
    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)
    assert result.column_grain is ColumnGrain.CAPTURED


# ── persistence: the refinement's failure mode must not wipe stored pairs ─────────────────


@pytest.fixture
def sf_connection(db_session: Session) -> Connection:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@x.io")
    db_session.add(user)
    db_session.flush()
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config=dict(_CONFIG),
        secret_ref="ref",
        created_by=user.id,
    )
    db_session.add(conn)
    db_session.flush()
    return conn


_STG = ("DATAQ_DB.RETAIL.ORDERS_HEADER", "DATAQ_DB.ANALYTICS_STG.STG_ORDERS")


def _stored(db_session: Session, connection: Connection) -> dict[tuple[str, str], Any]:
    up, down = aliased(Asset), aliased(Asset)
    rows = db_session.execute(
        select(up.name, down.name, LineageEdge.columns, LineageEdge.column_grain)
        .join(up, up.id == LineageEdge.upstream_asset_id)
        .join(down, down.id == LineageEdge.downstream_asset_id)
        .where(LineageEdge.connection_id == connection.id)
    ).all()
    return {(u, d): (cols, grain) for u, d, cols, grain in rows}


def _pull(db_session: Session, connection: Connection, conn: Any) -> None:
    outcome = refresh_warehouse_edges(
        db_session, connection=connection, provider=SnowflakeLineageProvider(), conn=conn
    )
    assert outcome is not None


def _captured_pull(db_session: Session, connection: Connection) -> None:
    _pull(
        db_session,
        connection,
        _gl_orders_conn(
            results={"ACCESS_HISTORY ah": [_ah_row(*_STG, [("CUSTOMER_ID", "CUSTOMER_ID")])]}
        ),
    )
    key = (_sf("RETAIL", "ORDERS_HEADER"), _sf("ANALYTICS_STG", "STG_ORDERS"))
    assert _stored(db_session, connection)[key] == ([["CUSTOMER_ID", "CUSTOMER_ID"]], "captured")


def test_a_transient_refinement_failure_keeps_previously_stored_pairs(
    db_session: Session, sf_connection: Connection
) -> None:
    _captured_pull(db_session, sf_connection)
    result = SnowflakeLineageProvider().fetch_edges(
        _gl_orders_conn(raises={"ACCESS_HISTORY": RuntimeError("blip")}), connection_config=_CONFIG
    )
    assert result.columns_authoritative is False and result.prunable is True
    _pull(db_session, sf_connection, _gl_orders_conn(raises={"ACCESS_HISTORY": RuntimeError("b")}))
    key = (_sf("RETAIL", "ORDERS_HEADER"), _sf("ANALYTICS_STG", "STG_ORDERS"))
    # Pairs survive the blip, and so does the evidence that a pull once looked.
    assert _stored(db_session, sf_connection)[key] == (
        [["CUSTOMER_ID", "CUSTOMER_ID"]],
        "captured",
    )


def test_a_confirmed_denial_clears_pairs_rather_than_freezing_them(
    db_session: Session, sf_connection: Connection
) -> None:
    """The #911 rule: a revoked grant is permanent, so the pairs must not freeze at revocation."""
    _captured_pull(db_session, sf_connection)
    _pull(
        db_session,
        sf_connection,
        _gl_orders_conn(raises={"ACCESS_HISTORY": _feature_unsupported_error()}),
    )
    key = (_sf("RETAIL", "ORDERS_HEADER"), _sf("ANALYTICS_STG", "STG_ORDERS"))
    assert _stored(db_session, sf_connection)[key] == (None, "unavailable")


# ─────────────── COLUMN-domain GET_LINEAGE for views / dynamic tables (#2106) ───────────────

_NS = "snowflake://pvqsoeq-zgb34383"


def _id(name: str) -> Any:
    from backend.app.services.asset_identity import AssetIdentity

    return AssetIdentity(namespace=_NS, name=name)


def _column_row(src: str, src_col: str, dst: str, dst_col: str) -> tuple[Any, ...]:
    sdb, ssch, stab = src.split(".")
    ddb, dsch, dtab = dst.split(".")
    return (sdb, ssch, stab, "VIEW", "ACTIVE", src_col, ddb, dsch, dtab, "VIEW", "ACTIVE", dst_col)


class _ColumnConn:
    """Answers the INFORMATION_SCHEMA column listing and per-column GET_LINEAGE calls."""

    def __init__(
        self,
        columns: list[tuple[str, str, str]],
        lineage: dict[str, list[tuple[Any, ...]]],
        fail: set[str] | None = None,
    ) -> None:
        self.columns = columns
        self.lineage = lineage
        self.fail = fail or set()
        self.seeds: list[str] = []

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        sql = str(statement)
        conn = self

        class _Result:
            def all(self) -> list[Any]:
                if "INFORMATION_SCHEMA.COLUMNS" in sql:
                    return list(conn.columns)
                assert "'COLUMN', :dir, 1" in sql and (params or {})["dir"] == "UPSTREAM"
                seed = (params or {})["obj"]
                conn.seeds.append(seed)
                if seed in conn.fail:
                    raise RuntimeError("boom")
                return conn.lineage.get(seed, [])

        return _Result()


_V_STG = "DATAQ_DB.ANALYTICS_STG.STG_ORDERS"
_V_MART = "DATAQ_DB.ANALYTICS.MART_ORDER_REVENUE"
_V_RAW = "DATAQ_DB.RETAIL.ORDERS_HEADER"


def _refine(conn: Any, edges: Any, *, cap: int = 300) -> Any:
    from unittest import mock

    from backend.app.lineage.warehouse import LineageEdgePair  # noqa: F401

    with mock.patch(
        "backend.app.lineage.warehouse_snowflake.get_settings",
        return_value=mock.Mock(warehouse_lineage_max_column_seeds=cap),
    ):
        return SnowflakeLineageProvider()._refine_with_column_lineage(
            conn, _NS, "DATAQ_DB", edges, ColumnGrain.CAPTURED, None
        )


def test_view_edges_gain_column_pairs_from_column_domain_lineage() -> None:
    """The live shape (2026-09-28): a view renames as it derives — ORDER_TOTAL feeds
    LIFETIME_VALUE — which only COLUMN-domain GET_LINEAGE can see."""
    from backend.app.lineage.warehouse import LineageEdgePair

    edges = (LineageEdgePair(upstream=_id(_V_STG), downstream=_id(_V_MART)),)
    conn = _ColumnConn(
        columns=[("ANALYTICS", "MART_ORDER_REVENUE", "REVENUE"), ("RETAIL", "OTHER", "X")],
        lineage={
            "DATAQ_DB.ANALYTICS.MART_ORDER_REVENUE.REVENUE": [
                _column_row(_V_STG, "ORDER_TOTAL", _V_MART, "REVENUE")
            ]
        },
    )

    (edge,), grain, note = _refine(conn, edges)

    assert edge.column_pairs == (("ORDER_TOTAL", "REVENUE"),)
    assert grain == ColumnGrain.CAPTURED and note is None
    # Only the bare edge's downstream is seeded — never every column in the database.
    assert conn.seeds == ["DATAQ_DB.ANALYTICS.MART_ORDER_REVENUE.REVENUE"]


def test_column_lineage_never_adds_a_table_edge() -> None:
    """A pair whose TABLE edge the traversal did not return stays out, exactly like the
    ACCESS_HISTORY pass: this refinement cannot change what the table-level prune sees."""
    from backend.app.lineage.warehouse import LineageEdgePair

    edges = (LineageEdgePair(upstream=_id(_V_STG), downstream=_id(_V_MART)),)
    conn = _ColumnConn(
        columns=[("ANALYTICS", "MART_ORDER_REVENUE", "REVENUE")],
        lineage={
            "DATAQ_DB.ANALYTICS.MART_ORDER_REVENUE.REVENUE": [
                _column_row(_V_RAW, "ORDER_TOTAL", _V_MART, "REVENUE")
            ]
        },
    )
    (edge,), _grain, _note = _refine(conn, edges)
    assert edge.upstream.name == _V_STG and edge.column_pairs == ()


def test_an_edge_that_already_has_pairs_is_not_reseeded() -> None:
    from backend.app.lineage.warehouse import LineageEdgePair

    edges = (
        LineageEdgePair(upstream=_id(_V_STG), downstream=_id(_V_MART), column_pairs=(("A", "B"),)),
    )
    conn = _ColumnConn(columns=[("ANALYTICS", "MART_ORDER_REVENUE", "REVENUE")], lineage={})
    result, _grain, _note = _refine(conn, edges)
    assert result == edges and conn.seeds == []


def test_the_column_seed_cap_truncates_loudly() -> None:
    from backend.app.lineage.warehouse import LineageEdgePair

    edges = (LineageEdgePair(upstream=_id(_V_STG), downstream=_id(_V_MART)),)
    conn = _ColumnConn(
        columns=[("ANALYTICS", "MART_ORDER_REVENUE", f"C{i}") for i in range(5)], lineage={}
    )
    _edges, _grain, note = _refine(conn, edges, cap=2)
    assert len(conn.seeds) == 2
    assert note is not None and "truncated at 2 column seeds" in note


def test_failed_column_calls_are_counted_not_fatal() -> None:
    from backend.app.lineage.warehouse import LineageEdgePair

    edges = (LineageEdgePair(upstream=_id(_V_STG), downstream=_id(_V_MART)),)
    conn = _ColumnConn(
        columns=[
            ("ANALYTICS", "MART_ORDER_REVENUE", "A"),
            ("ANALYTICS", "MART_ORDER_REVENUE", "B"),
        ],
        lineage={
            "DATAQ_DB.ANALYTICS.MART_ORDER_REVENUE.B": [_column_row(_V_STG, "B", _V_MART, "B")]
        },
        fail={"DATAQ_DB.ANALYTICS.MART_ORDER_REVENUE.A"},
    )
    (edge,), _grain, note = _refine(conn, edges)
    assert edge.column_pairs == (("B", "B"),)
    assert note is not None and "1 of 2 column call(s) failed" in note


def test_a_zero_cap_disables_the_pass() -> None:
    from backend.app.lineage.warehouse import LineageEdgePair

    edges = (LineageEdgePair(upstream=_id(_V_STG), downstream=_id(_V_MART)),)
    conn = _ColumnConn(columns=[("ANALYTICS", "MART_ORDER_REVENUE", "A")], lineage={})
    result, _grain, _note = _refine(conn, edges, cap=0)
    assert result == edges and conn.seeds == []


@pytest.mark.parametrize(
    ("part", "quoted"),
    [("ORDERS", "ORDERS"), ("orders", '"orders"'), ("My Col", '"My Col"'), ('a"b', '"a""b"')],
)
def test_seed_identifiers_quote_anything_not_unquoted_upper(part: str, quoted: str) -> None:
    from backend.app.lineage.warehouse_snowflake import _quote_ident

    assert _quote_ident(part) == quoted


def test_fetch_edges_runs_the_column_pass_on_the_get_lineage_tier() -> None:
    """Wired, not just callable: the table edge from the real TABLE-domain capture gains its
    column pairs through `fetch_edges` itself."""
    conn = _GetLineageConn(
        {
            ("DATAQ_DB.RETAIL.ORDERS_HEADER", "DOWNSTREAM"): _get_lineage_rows(
                "gl_down_orders_header"
            ),
            ("DATAQ_DB.ANALYTICS_STG.STG_ORDERS.CUSTOMER_ID", "UPSTREAM"): [
                _column_row(
                    "DATAQ_DB.RETAIL.ORDERS_HEADER",
                    "CUSTOMER_ID",
                    "DATAQ_DB.ANALYTICS_STG.STG_ORDERS",
                    "CUSTOMER_ID",
                )
            ],
        },
        results={"INFORMATION_SCHEMA.COLUMNS": [("ANALYTICS_STG", "STG_ORDERS", "CUSTOMER_ID")]},
    )

    result = SnowflakeLineageProvider().fetch_edges(conn, connection_config=_CONFIG)

    by_pair = {(e.upstream.name, e.downstream.name): e for e in result.edges}
    edge = by_pair[(_sf("RETAIL", "ORDERS_HEADER"), _sf("ANALYTICS_STG", "STG_ORDERS"))]
    assert ("CUSTOMER_ID", "CUSTOMER_ID") in edge.column_pairs
