"""The declared residency posture — G4 / #434, GDPR Ch. V.

`GET /api/v1/admin/deployment`. Two properties matter and neither is obvious from
the happy path: an **undeclared** region must read as undeclared rather than as a
default, and the transfer list must be **enumerated** rather than derived from
whatever happens to be configured, so a vector that is switched off still appears.

Skips without TEST_DATABASE_URL.
"""

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
    """Gated, and asserted rather than inherited from the router decorator.

    This endpoint enumerates the deployment's outbound integrations — useful to an
    auditor and equally useful to someone deciding where to aim.
    """
    app.dependency_overrides[get_current_user] = lambda: _user(db_session, role)
    resp = client.get("/api/v1/admin/deployment")
    assert resp.status_code == 403


def test_an_undeclared_region_reads_as_undeclared(client: TestClient, monkeypatch: Any) -> None:
    """`null`, never a default.

    A guessed region on a compliance surface is worse than a blank one: an auditor
    reading "West US 2" cannot tell it was inferred, and would record a
    jurisdiction nobody ever declared.
    """
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
    """Enumerated, not derived.

    The LLM seam does not exist yet, and listing it as `enabled: false` is the
    point: an auditor should see that it was considered and is off, rather than
    having to infer its absence from a list that only shows what is switched on.
    A vector that appears only once someone remembers to add it is not a control.
    """
    body = client.get("/api/v1/admin/deployment").json()
    names = {t["name"] for t in body["external_transfers"]}
    assert {"alert_delivery", "llm_intelligence", "telemetry"} <= names

    llm = next(t for t in body["external_transfers"] if t["name"] == "llm_intelligence")
    assert llm["enabled"] is False
    assert len(llm["detail"]) > 40, "a vector with no explanation is a row, not a disclosure"


def test_every_transfer_vector_explains_itself(client: TestClient) -> None:
    """A name and a boolean tell an auditor nothing about what actually leaves.

    Each entry has to say what it carries and who chose the destination — for two
    of the three, the endpoint is operator-configured and its location is outside
    DataQ's knowledge, which is a fact that must be stated rather than glossed.
    """
    body = client.get("/api/v1/admin/deployment").json()
    thin = [t["name"] for t in body["external_transfers"] if len(t["detail"].strip()) < 40]
    assert not thin, f"transfer vectors with no substantive detail: {thin}"
