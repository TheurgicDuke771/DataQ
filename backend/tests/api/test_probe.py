"""Probe endpoint tests against a real Postgres (db_session) via TestClient.

get_db is overridden to the test session so requests share the rolled-back
transaction; run_dispatch.dispatch_run is spied so no broker is needed. Auth runs
in dev-bypass mode (conftest), which upserts the dev user into the same session.
"""

import uuid
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.core.auth import get_current_user
from backend.app.db.models import Check, Connection, Result, Run, Suite
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import run_dispatch
from backend.app.services.probe import PROBE_CONNECTION_NAME, PROBE_SUITE_NAME


@pytest.fixture
def probe_client(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, list[Any]]]:
    app.dependency_overrides[get_db] = lambda: db_session
    delay_calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(run_dispatch, "dispatch_run", lambda *args, **_kw: delay_calls.append(args))
    try:
        yield TestClient(app), delay_calls
    finally:
        app.dependency_overrides.clear()


# ───────────────────────── POST ────────────────────────────────────


def test_post_creates_queued_run_and_dispatches(
    probe_client: tuple[TestClient, list[Any]], db_session: Any
) -> None:
    client, delay_calls = probe_client
    resp = client.post("/api/v1/_probe/snowflake-suite")

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"

    run = db_session.get(Run, uuid.UUID(body["run_id"]))
    assert run is not None and run.status == "queued"
    assert run.triggered_by.startswith("probe:")

    # fixtures seeded
    assert db_session.scalars(
        select(Connection).where(Connection.name == PROBE_CONNECTION_NAME)
    ).first()

    # dispatched once with (run_id,) — the worker resolves the target (#215)
    assert len(delay_calls) == 1
    assert str(delay_calls[0][0]) == body["run_id"]


def test_post_dispatch_failure_marks_run_failed(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Broker unreachable: the run must not be left stuck 'queued'."""
    app.dependency_overrides[get_db] = lambda: db_session

    def _boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("broker down")

    monkeypatch.setattr(run_dispatch, "dispatch_run", _boom)
    try:
        resp = TestClient(app).post("/api/v1/_probe/snowflake-suite")
        assert resp.status_code == 503
        run = db_session.scalars(select(Run)).first()
        assert run is not None and run.status == "failed"
        # #227: probe now uses the canonical dispatch-failed shape — finished_at
        # set (was NULL before), started_at left NULL (it never started).
        assert run.finished_at is not None
        assert run.started_at is None
    finally:
        app.dependency_overrides.clear()


def test_post_is_idempotent_across_calls(
    probe_client: tuple[TestClient, list[Any]], db_session: Any
) -> None:
    client, _ = probe_client
    client.post("/api/v1/_probe/snowflake-suite")
    client.post("/api/v1/_probe/snowflake-suite")

    assert (
        len(
            db_session.scalars(
                select(Connection).where(Connection.name == PROBE_CONNECTION_NAME)
            ).all()
        )
        == 1
    )
    assert len(db_session.scalars(select(Suite).where(Suite.name == PROBE_SUITE_NAME)).all()) == 1
    assert len(db_session.scalars(select(Check)).all()) == 1
    # two runs, though
    assert len(db_session.scalars(select(Run)).all()) == 2


# ───────────────────────── GET ─────────────────────────────────────


def test_get_returns_run_with_results(
    probe_client: tuple[TestClient, list[Any]], db_session: Any
) -> None:
    client, _ = probe_client
    run_id = client.post("/api/v1/_probe/snowflake-suite").json()["run_id"]

    # simulate the worker having persisted a result
    check = db_session.scalars(select(Check)).first()
    db_session.add(
        Result(
            run_id=uuid.UUID(run_id),
            check_id=check.id,
            status="warn",
            metric_value=Decimal("2.5"),
            observed_value={"observed_value": 5},
            expected_value={"min_value": 1},
        )
    )
    db_session.commit()

    resp = client.get(f"/api/v1/_probe/runs/{run_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued"
    assert len(body["results"]) == 1
    assert body["results"][0]["status"] == "warn"
    assert body["results"][0]["metric_value"] == 2.5  # surfaced as a JSON number
    assert body["results"][0]["observed_value"] == {"observed_value": 5}


def test_get_unknown_run_returns_404(probe_client: tuple[TestClient, list[Any]]) -> None:
    client, _ = probe_client
    resp = client.get(f"/api/v1/_probe/runs/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_get_run_without_results_returns_empty_list(
    probe_client: tuple[TestClient, list[Any]],
) -> None:
    client, _ = probe_client
    run_id = client.post("/api/v1/_probe/snowflake-suite").json()["run_id"]
    # No worker ran (delay is spied), so the queued run has no results yet.
    resp = client.get(f"/api/v1/_probe/runs/{run_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued"
    assert body["results"] == []


def test_get_malformed_run_id_returns_422(probe_client: tuple[TestClient, list[Any]]) -> None:
    client, _ = probe_client
    resp = client.get("/api/v1/_probe/runs/not-a-uuid")
    assert resp.status_code == 422


# ───────────────────────── auth gating ─────────────────────────────


def test_post_requires_auth(db_session: Any) -> None:
    """The handler must not run (no Run created) when auth rejects the request."""
    app.dependency_overrides[get_db] = lambda: db_session

    def _reject() -> None:
        raise HTTPException(status_code=401, detail="unauthorized")

    app.dependency_overrides[get_current_user] = _reject
    try:
        resp = TestClient(app).post("/api/v1/_probe/snowflake-suite")
        assert resp.status_code == 401
        assert db_session.scalars(select(Run)).all() == []
    finally:
        app.dependency_overrides.clear()
