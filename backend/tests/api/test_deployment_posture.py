"""The declared residency posture — G4 / #434, GDPR Ch. V."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.core.auth import get_current_user
from backend.app.core.config import get_settings
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.main import app


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def _user(db_session: Any, role: str) -> User:
    user = User(
        aad_object_id=uuid.uuid4().hex,
        email=f"{role}-{uuid.uuid4().hex[:8]}@example.com",
        role=role,
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_a_non_admin_is_refused(client: TestClient, db_session: Any, role: str) -> None:
    """Gated, and asserted rather than inherited from the router decorator."""
    app.dependency_overrides[get_current_user] = lambda: _user(db_session, role)
    resp = client.get("/api/v1/admin/deployment")
    assert resp.status_code == 403


def test_an_undeclared_region_reads_as_undeclared(client: TestClient, monkeypatch: Any) -> None:
    """`null`, never a default."""
    monkeypatch.setenv("DEPLOYMENT_REGION", "")
    get_settings.cache_clear()
    body = client.get("/api/v1/admin/deployment").json()
    assert body["region"] is None


def test_a_declared_region_is_surfaced(client: TestClient, monkeypatch: Any) -> None:
    """The point of the endpoint: readable without shell access to the deployment."""
    monkeypatch.setenv("DEPLOYMENT_REGION", "westeurope")
    get_settings.cache_clear()
    body = client.get("/api/v1/admin/deployment").json()
    assert body["region"] == "westeurope"


def test_a_disabled_transfer_vector_is_still_listed(client: TestClient) -> None:
    """Enumerated, not derived."""
    body = client.get("/api/v1/admin/deployment").json()
    names = {t["name"] for t in body["external_transfers"]}
    assert {"alert_delivery", "llm_intelligence", "telemetry"} <= names

    llm = next(t for t in body["external_transfers"] if t["name"] == "llm_intelligence")
    assert llm["enabled"] is False
    assert len(llm["detail"]) > 40, "a vector with no explanation is a row, not a disclosure"


def test_every_transfer_vector_explains_itself(client: TestClient) -> None:
    """A name and a boolean tell an auditor nothing about what actually leaves."""
    body = client.get("/api/v1/admin/deployment").json()
    thin = [t["name"] for t in body["external_transfers"] if len(t["detail"].strip()) < 40]
    assert not thin, f"transfer vectors with no substantive detail: {thin}"


def test_the_live_mcp_transfer_vector_is_listed(client: TestClient) -> None:
    """Two distinct LLM vectors, and conflating them was a real omission."""
    body = client.get("/api/v1/admin/deployment").json()
    names = {t["name"] for t in body["external_transfers"]}
    assert "mcp_ai_clients" in names
    assert "signin_email" in names
    assert "secret_store" in names


def test_per_suite_alerting_counts_as_an_alert_transfer(
    client: TestClient, db_session: Any
) -> None:
    """Reading only the WORKSPACE settings reported "no alert transfer" for a
    deployment that alerts entirely through per-suite webhooks.
    """
    from backend.app.db.models import Connection, Suite, SuiteNotification

    before = next(
        t
        for t in client.get("/api/v1/admin/deployment").json()["external_transfers"]
        if t["name"] == "alert_delivery"
    )
    assert before["enabled"] is False, "precondition: no workspace-level alerting configured"

    owner = _user(db_session, "admin")
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "x"},
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(name=f"s-{uuid.uuid4().hex[:8]}", connection_id=conn.id, created_by=owner.id)
    db_session.add(suite)
    db_session.flush()
    db_session.add(
        SuiteNotification(suite_id=suite.id, enabled=True, webhook_secret_ref="suite-notif-x")
    )
    db_session.commit()

    after = next(
        t
        for t in client.get("/api/v1/admin/deployment").json()["external_transfers"]
        if t["name"] == "alert_delivery"
    )
    assert (
        after["enabled"] is True
    ), "a suite-level webhook is an outbound transfer and must be reported as one"
