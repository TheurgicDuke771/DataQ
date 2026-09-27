"""REST surface of column-grain lineage (#1710): the trace endpoint + per-edge coverage."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.core.auth import get_current_user
from backend.app.db.models import Connection, LineageEdge, User
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services.asset_service import upsert_assets

_NS = "snowflake://ab12345.eu-west-1"


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _user(db_session: Any, *, role: str = "member") -> User:
    u = User(aad_object_id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex[:8]}@x.io", role=role)
    db_session.add(u)
    db_session.flush()
    app.dependency_overrides[get_current_user] = lambda: u
    return u


@pytest.fixture
def world(db_session: Any) -> dict[str, Any]:
    owner = _user(db_session)
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "ab12345.eu-west-1", "database": "DB"},
        secret_ref="kv-x",
        created_by=owner.id,
        lineage_column_grain="captured",
    )
    db_session.add(conn)
    db_session.flush()
    names = ["DB.RAW.ORDERS", "DB.STG.ORDERS", "DB.MART.REVENUE", "DB.RAW.FX"]
    ids = upsert_assets(db_session, [{"namespace": _NS, "name": n} for n in names])
    a = {n: ids[(_NS, n)] for n in names}

    def edge(up: str, down: str, columns: Any, source: str = "snowflake") -> None:
        db_session.add(
            LineageEdge(
                upstream_asset_id=a[up],
                downstream_asset_id=a[down],
                source=source,
                connection_id=conn.id,
                columns=columns,
            )
        )

    edge("DB.RAW.ORDERS", "DB.STG.ORDERS", [["CUSTOMER_ID", "CUSTOMER_ID"], ["AMT", "AMOUNT"]])
    edge("DB.STG.ORDERS", "DB.MART.REVENUE", [["AMOUNT", "REVENUE"]])
    edge("DB.RAW.FX", "DB.MART.REVENUE", None, source="dbt")
    db_session.commit()
    return {"conn": conn, "a": a}


def _get(client: TestClient, asset_id: uuid.UUID, **params: Any) -> Any:
    return client.get(f"/api/v1/assets/{asset_id}/column-lineage", params=params)


def test_trace_upstream_names_every_asset_and_flags_the_gap(
    client: TestClient, world: dict[str, Any]
) -> None:
    a = world["a"]
    resp = _get(client, a["DB.MART.REVENUE"], column="revenue", direction="upstream")
    assert resp.status_code == 200
    body = resp.json()
    assert body["upstream_status"] == "traced"
    assert body["downstream_status"] is None
    assert [(n["column"], n["depth"]) for n in body["upstream"]] == [("AMOUNT", 1), ("AMT", 2)]
    assert [(o["column"], o["confirmed"]) for o in body["origins"]] == [("AMT", True)]
    # The dbt edge FX → REVENUE carries no columns: REVENUE may derive from it.
    assert body["gaps"] == [
        {
            "upstream_asset_id": str(a["DB.RAW.FX"]),
            "downstream_asset_id": str(a["DB.MART.REVENUE"]),
            "coverage": "not_captured",
        }
    ]
    assert body["complete"] is False
    assert {x["name"] for x in body["assets"]} == set(world["a"])


def test_trace_downstream(client: TestClient, world: dict[str, Any]) -> None:
    a = world["a"]
    body = _get(client, a["DB.RAW.ORDERS"], column="AMT", direction="downstream").json()
    assert [(n["column"], n["depth"]) for n in body["downstream"]] == [
        ("AMOUNT", 1),
        ("REVENUE", 2),
    ]
    assert body["complete"] is True
    assert body["origins"] == []  # not requested upstream


def test_viewer_can_trace_workspace_visible_topology(
    client: TestClient, world: dict[str, Any], db_session: Any
) -> None:
    _user(db_session, role="viewer")
    resp = _get(client, world["a"]["DB.STG.ORDERS"], column="CUSTOMER_ID")
    assert resp.status_code == 200


def test_trace_inherits_table_level_qualifiers(
    client: TestClient, world: dict[str, Any], db_session: Any
) -> None:
    conn = world["conn"]
    conn.lineage_last_refresh_at = datetime.now(UTC)
    conn.lineage_degraded_reason = "column detail unavailable — access_history: call failed"
    db_session.commit()
    body = _get(client, world["a"]["DB.STG.ORDERS"], column="AMOUNT").json()
    assert any("column detail unavailable" in q for q in body["qualified_by"])


@pytest.mark.parametrize(
    "params",
    [
        {"column": ""},
        {"column": "x" * 256},
        {"column": "x", "direction": "sideways"},
        {"column": "x", "max_depth": 0},
        {"column": "x", "max_depth": 26},
        {},
    ],
)
def test_bad_parameters_are_422(
    client: TestClient, world: dict[str, Any], params: dict[str, Any]
) -> None:
    resp = _get(client, world["a"]["DB.STG.ORDERS"], **params)
    assert resp.status_code == 422


def test_unknown_asset_is_404_and_garbage_id_422(client: TestClient, world: dict[str, Any]) -> None:
    missing = _get(client, uuid.uuid4(), column="x")
    assert missing.status_code == 404
    garbage = client.get("/api/v1/assets/nope/column-lineage?column=x")
    assert garbage.status_code == 422


def test_asset_detail_edges_carry_column_coverage(
    client: TestClient, world: dict[str, Any], db_session: Any
) -> None:
    a = world["a"]
    body = client.get(f"/api/v1/assets/{a['DB.MART.REVENUE']}").json()
    coverage = {
        (e["source"], e["target"]): (e["columns"], e["column_coverage"])
        for e in body["lineage_edges"]
    }
    assert coverage[(str(a["DB.STG.ORDERS"]), str(a["DB.MART.REVENUE"]))] == (
        [["AMOUNT", "REVENUE"]],
        "recorded",
    )
    assert coverage[(str(a["DB.RAW.FX"]), str(a["DB.MART.REVENUE"]))] == (None, "not_captured")


def test_asset_detail_unrecorded_grain_is_unknown_not_none_recorded(
    client: TestClient, world: dict[str, Any], db_session: Any
) -> None:
    a = world["a"]
    world["conn"].lineage_column_grain = None
    extra = upsert_assets(db_session, [{"namespace": _NS, "name": "DB.MART.OTHER"}])
    other = extra[(_NS, "DB.MART.OTHER")]
    db_session.add(
        LineageEdge(
            upstream_asset_id=a["DB.STG.ORDERS"],
            downstream_asset_id=other,
            source="snowflake",
            connection_id=world["conn"].id,
        )
    )
    db_session.commit()
    body = client.get(f"/api/v1/assets/{other}").json()
    (edge,) = [e for e in body["lineage_edges"] if e["target"] == str(other)]
    assert edge["columns"] is None and edge["column_coverage"] == "unknown"
