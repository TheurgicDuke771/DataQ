"""MCP `trace_column_lineage` + `get_asset`'s per-edge `column_coverage` (#1710)."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from fastmcp.exceptions import ToolError

from backend.app.db.models import Connection, LineageEdge
from backend.app.mcp import server
from backend.tests.mcp.test_mcp_tools import _as, _asset, _user


def _world(db_session: Any, *, grain: str | None = "captured") -> dict[str, Any]:
    owner = _user(db_session)
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "a"},
        secret_ref="kv",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    raw, stg, mart = (_asset(db_session, name=n) for n in ("RAW", "STG", "MART"))
    db_session.add_all(
        [
            LineageEdge(
                upstream_asset_id=raw.id,
                downstream_asset_id=stg.id,
                source="snowflake",
                connection_id=conn.id,
                columns=[["EMAIL", "EMAIL"]],
                column_grain=grain,
            ),
            LineageEdge(
                upstream_asset_id=stg.id,
                downstream_asset_id=mart.id,
                source="snowflake",
                connection_id=conn.id,
                column_grain=grain,
            ),
        ]
    )
    db_session.commit()
    return {"owner": owner, "raw": raw, "stg": stg, "mart": mart, "conn": conn}


def test_trace_reports_hops_origins_and_gaps(db_session: Any, monkeypatch: Any) -> None:
    w = _world(db_session)
    _as(monkeypatch, db_session, w["owner"])
    out = server.trace_column_lineage(str(w["stg"].id), "email")
    assert out["upstream_status"] == "traced"
    assert [(o["asset_id"], o["column"], o["confirmed"]) for o in out["origins"]] == [
        (str(w["raw"].id), "EMAIL", True)
    ]
    # STG → MART carries no pairs even though its source read column lineage fine: still a gap
    # (nothing recorded says EMAIL does or does not reach MART), never "nothing downstream".
    assert out["downstream_status"] == "incomplete"
    assert [g["coverage"] for g in out["gaps"]] == ["none_recorded"]
    assert out["complete"] is False
    assert {a["name"] for a in out["assets"]} == {"RAW", "STG", "MART"}  # gap endpoints named


def test_trace_downstream_of_a_grain_unknown_edge_is_incomplete(
    db_session: Any, monkeypatch: Any
) -> None:
    w = _world(db_session, grain=None)
    _as(monkeypatch, db_session, w["owner"])
    out = server.trace_column_lineage(str(w["stg"].id), "EMAIL", direction="downstream")
    assert out["downstream_status"] == "incomplete"
    assert out["gaps"] == [
        {
            "upstream_asset_id": str(w["stg"].id),
            "downstream_asset_id": str(w["mart"].id),
            "coverage": "unknown",
        }
    ]
    assert out["complete"] is False
    assert out["upstream_status"] is None


def test_trace_unknown_asset_is_a_tool_error(db_session: Any, monkeypatch: Any) -> None:
    _as(monkeypatch, db_session, _user(db_session))
    with pytest.raises(ToolError, match="asset not found"):
        server.trace_column_lineage(str(uuid.uuid4()), "x")
    with pytest.raises(ToolError):
        server.trace_column_lineage("not-a-uuid", "x")


def test_trace_schema_bounds_its_inputs() -> None:
    tool = asyncio.run(server.mcp.get_tool("trace_column_lineage"))
    assert tool is not None
    props = tool.parameters["properties"]
    assert props["column"]["minLength"] == 1 and props["column"]["maxLength"] == 255
    assert props["direction"]["enum"] == ["upstream", "downstream", "both"]
    assert props["max_depth"]["minimum"] == 1 and props["max_depth"]["maximum"] == 25


def test_get_asset_edges_carry_column_coverage(db_session: Any, monkeypatch: Any) -> None:
    w = _world(db_session)
    _as(monkeypatch, db_session, w["owner"])
    out = server.get_asset(str(w["stg"].id))
    by_target = {e["target"]: e for e in out["lineage"]["edges"]}
    assert by_target[str(w["stg"].id)]["column_coverage"] == "recorded"
    assert by_target[str(w["stg"].id)]["columns"] == (("EMAIL", "EMAIL"),)
    assert by_target[str(w["mart"].id)]["column_coverage"] == "none_recorded"
    assert by_target[str(w["mart"].id)]["columns"] is None


def test_get_asset_says_which_columns_are_masked_through_lineage(
    db_session: Any, monkeypatch: Any
) -> None:
    w = _world(db_session)
    w["raw"].column_tags = {"email": "sensitive"}
    db_session.commit()
    _as(monkeypatch, db_session, w["owner"])
    out = server.get_asset(str(w["stg"].id))
    assert out["inherited_classifications"] == [
        {
            "column": "email",
            "sources": [{"asset_id": str(w["raw"].id), "asset_name": "RAW", "column": "EMAIL"}],
        }
    ]
    assert out["inherited_classifications_truncated"] is False
