"""REST surface of automatic coverage (ADR 0047): excluding a table, and origin on suites/checks."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.core.auth import get_current_user
from backend.app.db.models import Asset, AuditEvent, Connection, User
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import coverage_service
from backend.app.services.asset_identity import resolve_asset_identity

_CONFIG = {"host": "wh.example.com", "database": "shop", "user": "reader", "auto_coverage": True}


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _as(db_session: Any, role: str) -> User:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex[:8]}@x.io", role=role)
    db_session.add(user)
    db_session.commit()
    app.dependency_overrides[get_current_user] = lambda: user
    return user


@pytest.fixture
def asset(db_session: Any) -> Asset:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex[:8]}@x.io")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"pg-{uuid.uuid4().hex[:6]}",
        type="postgres",
        env="dev",
        config=dict(_CONFIG),
        secret_ref="kv",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    ident = resolve_asset_identity("postgres", _CONFIG, {"schema": "public", "table": "orders"})
    now = datetime.now(UTC)
    row = Asset(
        namespace=ident.namespace,
        name=ident.name,
        env="dev",
        connection_id=conn.id,
        first_seen=now,
        last_seen=now,
    )
    db_session.add(row)
    db_session.commit()
    coverage_service.reconcile_connection(db_session, conn)
    return row


def test_an_admin_excludes_a_table_and_it_is_audited(
    client: TestClient, db_session: Any, asset: Asset
) -> None:
    admin = _as(db_session, "admin")
    resp = client.patch(f"/api/v1/assets/{asset.id}", json={"auto_coverage_excluded": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["auto_coverage_excluded"] is True
    event = db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "asset.update", AuditEvent.actor_user_id == admin.id
        )
    )
    assert event is not None and event.after["auto_coverage_excluded"] is True


def test_a_member_cannot_change_coverage(client: TestClient, db_session: Any, asset: Asset) -> None:
    _as(db_session, "member")
    resp = client.patch(f"/api/v1/assets/{asset.id}", json={"auto_coverage_excluded": True})
    assert resp.status_code == 403


def test_suites_and_checks_say_they_are_automatic(
    client: TestClient, db_session: Any, asset: Asset
) -> None:
    _as(db_session, "admin")
    suites = [s for s in client.get("/api/v1/suites").json() if s["origin"] == "auto"]
    assert len(suites) == 1 and suites[0]["created_by"] is None
    checks = client.get(f"/api/v1/suites/{suites[0]['id']}/checks").json()
    assert checks and {c["origin"] for c in checks} == {"auto"}
