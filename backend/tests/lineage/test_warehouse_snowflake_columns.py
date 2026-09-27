"""#1710: column grain on the Snowflake GET_LINEAGE tier (ACCESS_HISTORY refinement).

UNVERIFIED LIVE: the composition under test (GET_LINEAGE table edges + ACCESS_HISTORY column
pairs) could not be run against Snowflake — the harness credentials are expired (#2085). The
GET_LINEAGE rows are the real capture (all table-grain: NULL column names at TABLE domain, which
is WHY this refinement exists); the ACCESS_HISTORY `columns` blob shape is the #908 live-tuned
one. The live battery is recorded in the #1710 PR body.
"""

from __future__ import annotations

import json
from typing import Any

from backend.app.lineage.warehouse import ColumnGrain, LineageTier
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
    # A column-read failure is not a table-edge observation failure.
    assert result.prunable is True


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
