"""REST surface of the automatic-coverage review queue (ADR 0047)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.core.auth import get_current_user
from backend.app.db.models import Connection, Suite, User
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import suggestion_service


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
def queued(db_session: Any) -> Suite:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex[:8]}@x.io")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"pg-{uuid.uuid4().hex[:6]}",
        type="postgres",
        env="dev",
        config={"host": "h", "database": "shop", "user": "r"},
        secret_ref="kv",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(
        name="auto",
        connection_id=conn.id,
        target={"schema": "public", "table": "orders"},
        origin="auto",
    )
    db_session.add(suite)
    db_session.commit()
    for column in ("order_id", "status"):
        suggestion_service.propose(
            db_session,
            suite,
            source="profile",
            name=f"{column} is never null",
            expectation_type="expect_column_values_to_not_be_null",
            config={"column": column},
            rationale="No nulls in 500 rows.",
        )
    db_session.commit()
    return suite


def test_an_admin_reviews_the_queue(client: TestClient, db_session: Any, queued: Suite) -> None:
    _as(db_session, "admin")
    pending = client.get(f"/api/v1/suites/{queued.id}/suggestions").json()
    assert [s["name"] for s in pending] == ["order_id is never null", "status is never null"]
    assert pending[0]["rationale"] == "No nulls in 500 rows."

    accepted = client.post(f"/api/v1/suggestions/{pending[0]['id']}/accept")
    assert accepted.status_code == 200 and accepted.json()["check_id"]
    rejected = client.post(f"/api/v1/suggestions/{pending[1]['id']}/reject")
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"

    still_pending = client.get(f"/api/v1/suites/{queued.id}/suggestions").json()
    everything = client.get(f"/api/v1/suites/{queued.id}/suggestions?status=all").json()
    assert still_pending == []
    assert len(everything) == 2
    again = client.post(f"/api/v1/suggestions/{pending[1]['id']}/accept")
    assert again.status_code == 409


def test_a_viewer_cannot_decide(client: TestClient, db_session: Any, queued: Suite) -> None:
    _as(db_session, "admin")
    first = client.get(f"/api/v1/suites/{queued.id}/suggestions").json()[0]
    _as(db_session, "viewer")
    resp = client.post(f"/api/v1/suggestions/{first['id']}/accept")
    assert resp.status_code == 403


def test_an_unknown_suggestion_is_404(client: TestClient, db_session: Any) -> None:
    _as(db_session, "admin")
    resp = client.post(f"/api/v1/suggestions/{uuid.uuid4()}/reject")
    assert resp.status_code == 404
