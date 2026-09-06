"""`/admin/scoring` — the health-score weights (#1559)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.db.models import AuditEvent
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import scoring_settings_service as svc


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_read_reports_the_defaults_when_nothing_is_stored(client: TestClient) -> None:
    body = client.get("/api/v1/admin/scoring").json()
    assert body == {
        "warn": 0.5,
        "fail": 1.0,
        "critical": 2.0,
        "is_default": True,
        "defaults": {"warn": 0.5, "fail": 1.0, "critical": 2.0},
        "updated_by": None,
        "updated_at": None,
    }


def test_put_stores_audits_and_the_dashboard_reads_it(client: TestClient, db_session: Any) -> None:
    resp = client.put("/api/v1/admin/scoring", json={"warn": 0.5, "fail": 1.0, "critical": 1.0})
    assert resp.status_code == 200
    body = resp.json()
    assert (body["critical"], body["is_default"]) == (1.0, False)
    assert body["updated_by"] and body["updated_at"]
    assert svc.weights(db_session).critical == 1.0
    events = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "scoring_setting.update")
    ).all()
    assert len(events) == 1


def test_delete_resets_to_defaults(client: TestClient, db_session: Any) -> None:
    client.put("/api/v1/admin/scoring", json={"warn": 0.5, "fail": 1.0, "critical": 5.0})
    resp = client.delete("/api/v1/admin/scoring")
    assert resp.status_code == 200
    assert resp.json()["is_default"] is True
    assert svc.get_row(db_session) is None


def test_bad_ordering_is_a_422_with_the_code(client: TestClient) -> None:
    resp = client.put("/api/v1/admin/scoring", json={"warn": 1.5, "fail": 1.0, "critical": 2.0})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "scoring_weights_invalid"


def test_zero_critical_is_refused_at_the_schema(client: TestClient) -> None:
    resp = client.put("/api/v1/admin/scoring", json={"warn": 0.0, "fail": 0.0, "critical": 0.0})
    assert resp.status_code == 422


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_non_admins_are_refused(
    client: TestClient, as_role: Callable[..., tuple[Any, dict[str, str]]], role: str
) -> None:
    _, headers = as_role(role)
    got = client.get("/api/v1/admin/scoring", headers=headers)
    put = client.put(
        "/api/v1/admin/scoring", json={"warn": 0.5, "fail": 1.0, "critical": 2.0}, headers=headers
    )
    reset = client.delete("/api/v1/admin/scoring", headers=headers)
    assert (got.status_code, put.status_code, reset.status_code) == (403, 403, 403)
