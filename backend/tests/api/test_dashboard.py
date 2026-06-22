"""Dashboard summary API tests against a real Postgres (db_session).

get_db is overridden to the shared rolled-back session; get_current_user is
overridden per-test to act as a specific user. Verifies the endpoint shape,
suite-scoping (the data is already scoped in the service), window validation,
and a clean empty-workspace response. Skips without TEST_DATABASE_URL.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.core.auth import get_current_user
from backend.app.db.models import Check, Connection, Result, Run, Suite, User
from backend.app.db.session import get_db
from backend.app.main import app


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _as(user: User) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


def _user(db_session: Any) -> User:
    u = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:6]}@example.com")
    db_session.add(u)
    db_session.flush()
    return u


def _suite_with_results(db_session: Any, owner: User, statuses: list[str]) -> Suite:
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "a"},
        secret_ref="kv",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(name="audit", connection_id=conn.id, created_by=owner.id, target={"table": "T"})
    db_session.add(suite)
    db_session.flush()
    run = Run(suite_id=suite.id, status="succeeded", created_at=datetime.now(UTC))
    db_session.add(run)
    db_session.flush()
    for s in statuses:
        check = Check(
            suite_id=suite.id, name=f"c-{uuid.uuid4().hex[:6]}", expectation_type="e", config={}
        )
        db_session.add(check)
        db_session.flush()
        db_session.add(
            Result(run_id=run.id, check_id=check.id, status=s, created_at=datetime.now(UTC))
        )
    db_session.commit()
    return suite


def test_summary_returns_scoped_aggregates(client: TestClient, db_session: Any) -> None:
    alice = _user(db_session)
    _suite_with_results(db_session, alice, ["pass", "pass", "fail", "warn"])
    _as(alice)

    resp = client.get("/api/v1/dashboard/summary")
    assert resp.status_code == 200
    body = resp.json()

    assert body["window_days"] == 7
    # (0 + 0 + 1.0 + 0.5) / (4 * 2) = 0.1875 → 81.2
    assert body["kpis"]["health_score"] == 81.2
    assert body["kpis"]["pass_rate"] == 50.0
    assert body["kpis"]["total_runs"] == 1
    assert body["kpis"]["active_connections"] == 1
    assert len(body["suite_performance"]) == 1
    assert body["suite_performance"][0]["name"] == "audit"
    assert isinstance(body["trend"], list) and body["trend"]


def test_summary_honours_window_param(client: TestClient, db_session: Any) -> None:
    _as(_user(db_session))
    assert (
        client.get("/api/v1/dashboard/summary", params={"window_days": 30}).json()["window_days"]
        == 30
    )
    assert client.get("/api/v1/dashboard/summary", params={"window_days": 0}).status_code == 422
    assert client.get("/api/v1/dashboard/summary", params={"window_days": 91}).status_code == 422


def test_summary_empty_workspace_is_clean(client: TestClient, db_session: Any) -> None:
    _as(_user(db_session))
    body = client.get("/api/v1/dashboard/summary").json()
    # No runs/results → null KPIs (not 0/100 we can't justify), empty performance,
    # but a present (zero-filled) trend axis.
    assert body["kpis"]["health_score"] is None
    assert body["kpis"]["pass_rate"] is None
    assert body["kpis"]["total_runs"] == 0
    assert body["kpis"]["active_connections"] == 0
    assert body["suite_performance"] == []
    assert len(body["trend"]) >= 7
