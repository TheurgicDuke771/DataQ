"""Probe endpoint tests against a real Postgres (db_session) via TestClient.

get_db is overridden to the test session so requests share the rolled-back
transaction; run_dispatch.dispatch_run is spied so no broker is needed. Auth runs
in dev-bypass mode (conftest), which upserts the dev user into the same session.
"""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.core.auth import get_current_user
from backend.app.db.models import Check, Connection, Run, Suite
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


def test_probe_run_redacts_a_sensitive_monitor_cell() -> None:
    """This route reads the same `results` rows as `/runs/{id}/results`, so a cell
    masked there must be masked here too (#989).

    Found by the PR #1038 review: the redaction was wired at three sinks and this
    fourth one was missed — which is the failure mode of per-sink redaction, and
    the reason it needs a test per sink rather than one test for "the redactor".
    """
    from backend.app.services import run_service

    observed = {"error": "…not a parseable timestamp", "unparsed_value": "x@y.z", "column": "email"}
    redacted = run_service.redact_observed_value(observed, policy={"pii_columns": ["email"]})

    assert redacted is not None
    assert redacted["unparsed_value"] != "x@y.z"
    # The error text itself is safe by construction and must survive intact.
    assert redacted["error"] == "…not a parseable timestamp"


def test_the_unauthorized_probe_run_reader_is_gone(
    probe_client: tuple[TestClient, list[Any]],
) -> None:
    """`GET /_probe/runs/{id}` returned ANY run's results to ANY authenticated
    user — no suite-ownership check, only `get_current_user` (#1039).

    Deleted rather than gated: it was a second, weaker path to rows the real API
    already serves, and being a forgotten sibling is exactly how it escaped #989's
    redaction sweep. Two bugs on one route in one PR.

    Pinned as a 404 so it cannot be quietly reinstated — the next person to want a
    run reader has to add it where authz lives.
    """
    client, _ = probe_client
    resp = client.get(f"/api/v1/_probe/runs/{uuid.uuid4()}")

    assert resp.status_code == 404
