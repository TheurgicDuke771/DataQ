"""Check read surfaces show what an inheriting automatic baseline runs on (ADR 0047, #1661)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.db.models import Asset, Check, Connection, Suite, User
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.mcp import server as mcp_server
from backend.app.services import coverage_service as cov
from backend.app.services import coverage_settings_service as svc
from backend.app.services import suggestion_service, suite_io_service
from backend.app.services.asset_identity import resolve_asset_identity

NOW = datetime(2026, 9, 30, 6, 0, tzinfo=UTC)
_CONN = {"host": "wh.example.com", "database": "shop", "user": "reader", "auto_coverage": True}
_LOOSE = {"warn": 5, "fail": 7, "critical": 9}
KEY = svc.INHERIT_KEY


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def world(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr(suggestion_service, "refresh_from_profile", lambda *a, **k: 0)
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex[:8]}@x.io", role="admin")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"pg-{uuid.uuid4().hex[:6]}",
        type="postgres",
        env="dev",
        config=dict(_CONN),
        secret_ref="kv-pg",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    ident = resolve_asset_identity("postgres", _CONN, {"schema": "public", "table": "orders"})
    asset = Asset(
        namespace=ident.namespace,
        name=ident.name,
        env="dev",
        connection_id=conn.id,
        first_seen=NOW,
        last_seen=NOW,
    )
    db_session.add(asset)
    db_session.commit()
    cov.reconcile_connection(db_session, conn, now=NOW)
    suite = db_session.scalar(select(Suite).where(Suite.asset_id == asset.id))
    volume = db_session.scalar(
        select(Check).where(Check.suite_id == suite.id, Check.name == cov.VOLUME_CHECK)
    )
    return {"owner": owner, "conn": conn, "asset": asset, "suite": suite, "volume": volume}


def _volume(client: TestClient, world: dict[str, Any]) -> dict[str, Any]:
    resp = client.get(f"/api/v1/suites/{world['suite'].id}/checks/{world['volume'].id}")
    assert resp.status_code == 200
    body: dict[str, Any] = resp.json()
    return body


def test_an_inheriting_check_reads_the_values_in_effect_and_says_where_from(
    client: TestClient, world: dict[str, Any]
) -> None:
    before = _volume(client, world)

    client.put("/api/v1/admin/coverage", json={"sensitivity": {"volume": _LOOSE}})
    client.patch(
        f"/api/v1/assets/{world['asset'].id}",
        json={
            "auto_coverage_config": {
                "baseline": {"window": 3, "min_points": 3, "seasonality": False}
            }
        },
    )
    after = _volume(client, world)

    assert before["inherited"] == {"sensitivity": "default", "baseline": "default"}
    assert (before["warn_threshold"], before["config"]["window"]) == (3.0, 8)
    assert after["inherited"] == {"sensitivity": "workspace", "baseline": "asset"}
    assert (after["warn_threshold"], after["fail_threshold"], after["critical_threshold"]) == (
        5.0,
        7.0,
        9.0,
    )
    assert (after["config"]["window"], after["config"]["seasonality"]) == (3, False)


def test_the_list_agrees_with_the_single_read_and_leaves_other_checks_alone(
    client: TestClient, world: dict[str, Any]
) -> None:
    client.put("/api/v1/admin/coverage", json={"sensitivity": {"volume": _LOOSE}})

    listed = {c["name"]: c for c in client.get(f"/api/v1/suites/{world['suite'].id}/checks").json()}

    assert listed[cov.VOLUME_CHECK] == _volume(client, world)
    assert listed[cov.PROFILE_CHECK]["warn_threshold"] == 3.0
    assert listed[cov.SCHEMA_CHECK]["inherited"] is None


def test_editing_a_threshold_over_rest_returns_the_check_with_its_own_values(
    client: TestClient, world: dict[str, Any]
) -> None:
    client.put("/api/v1/admin/coverage", json={"sensitivity": {"volume": _LOOSE}})

    resp = client.patch(
        f"/api/v1/suites/{world['suite'].id}/checks/{world['volume'].id}",
        json={"warn_threshold": 6},
    )
    client.put(
        "/api/v1/admin/coverage",
        json={"sensitivity": {"volume": {"warn": 1, "fail": 2, "critical": 3}}},
    )
    later = _volume(client, world)

    assert resp.status_code == 200
    assert resp.json()["inherited"] is None
    assert resp.json()["config"][KEY] is False
    # Pinned at what was in effect, and a later change of the setting no longer reaches it.
    assert (later["warn_threshold"], later["fail_threshold"], later["critical_threshold"]) == (
        6.0,
        7.0,
        9.0,
    )


def test_a_user_authored_check_cannot_claim_to_inherit_over_rest(
    client: TestClient, world: dict[str, Any], db_session: Any
) -> None:
    suite = Suite(
        name="manual",
        connection_id=world["conn"].id,
        created_by=world["owner"].id,
        target={"schema": "public", "table": "orders"},
    )
    db_session.add(suite)
    db_session.commit()

    resp = client.post(
        f"/api/v1/suites/{suite.id}/checks",
        json={
            "name": "vol",
            "kind": "anomaly",
            "expectation_type": "monitor:anomaly",
            "config": {"target_metric": "row_count", KEY: True},
            "fail_threshold": 4,
        },
    )

    assert resp.status_code == 422
    assert "automatic baseline" in resp.json()["error"]["message"]


def test_the_mcp_summary_shows_the_values_in_effect_and_the_level(
    db_session: Any, world: dict[str, Any]
) -> None:
    svc.set_workspace(db_session, {"sensitivity": {"volume": _LOOSE}}, actor=world["owner"])
    db_session.commit()
    volume = world["volume"]

    summary = mcp_server._check_summary(
        volume, mcp_server._inherited(db_session, [volume]).get(volume.id)
    )
    without = mcp_server._check_summary(volume)

    assert (summary["warn_threshold"], summary["critical_threshold"]) == (5.0, 9.0)
    assert summary["inherited_from"] == {"sensitivity": "workspace", "baseline": "default"}
    assert (without["warn_threshold"], without["inherited_from"]) == (3.0, None)


def test_importing_an_exported_automatic_suite_makes_ordinary_checks(
    db_session: Any, world: dict[str, Any]
) -> None:
    document = suite_io_service.export_suite(db_session, world["suite"])
    exported = {c["name"]: c for c in document["checks"]}

    imported = suite_io_service.import_suite(
        db_session,
        version=document["version"],
        name="copy",
        description=None,
        checks=document["checks"],
        connection_id=world["conn"].id,
        created_by=world["owner"].id,
    )

    # The document carries the flag; a person's suite cannot inherit, so the copy drops it.
    assert exported[cov.VOLUME_CHECK]["config"][KEY] is True
    assert all(KEY not in c.config for c in imported.checks)
    assert all(not svc.inherits(c) for c in imported.checks)
