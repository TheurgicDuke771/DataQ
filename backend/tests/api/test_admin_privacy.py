"""`/admin/privacy` — the zero-sample toggle (#1887)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.core.config import get_settings
from backend.app.db.models import AuditEvent
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import privacy_settings_service as svc


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _fresh_settings() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _force_env(monkeypatch: pytest.MonkeyPatch, value: bool) -> None:
    monkeypatch.setenv("PRIVACY_ZERO_SAMPLE_MODE", "true" if value else "false")
    get_settings.cache_clear()


def test_read_reports_off_when_nothing_is_set(client: TestClient, monkeypatch: Any) -> None:
    _force_env(monkeypatch, False)
    body = client.get("/api/v1/admin/privacy").json()
    assert body == {
        "effective": False,
        "stored": False,
        "source": "off",
        "env_forced": False,
        "updated_by": None,
        "updated_at": None,
    }


def test_put_turns_it_on_and_is_audited(
    client: TestClient, db_session: Any, monkeypatch: Any
) -> None:
    _force_env(monkeypatch, False)
    resp = client.put("/api/v1/admin/privacy", json={"zero_sample_mode": True})
    assert resp.status_code == 200
    body = resp.json()
    assert (body["effective"], body["stored"], body["source"]) == (True, True, "db")
    assert body["updated_by"] and body["updated_at"]
    assert svc.zero_sample_mode(db_session) is True
    events = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "privacy_setting.update")
    ).all()
    assert len(events) == 1


def test_env_forced_reads_env_and_refuses_off(
    client: TestClient, db_session: Any, monkeypatch: Any
) -> None:
    _force_env(monkeypatch, True)
    body = client.get("/api/v1/admin/privacy").json()
    assert (body["effective"], body["source"], body["env_forced"]) == (True, "env", True)
    off = client.put("/api/v1/admin/privacy", json={"zero_sample_mode": False})
    assert off.status_code == 409
    assert off.json()["error"]["code"] == "zero_sample_env_forced"
    on = client.put("/api/v1/admin/privacy", json={"zero_sample_mode": True})
    assert on.status_code == 200
    assert svc.stored_zero_sample_mode(db_session) is True


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_non_admins_are_refused(
    client: TestClient, as_role: Callable[..., tuple[Any, dict[str, str]]], role: str
) -> None:
    _, headers = as_role(role)
    got = client.get("/api/v1/admin/privacy", headers=headers)
    put = client.put("/api/v1/admin/privacy", json={"zero_sample_mode": True}, headers=headers)
    assert (got.status_code, put.status_code) == (403, 403)


def test_posture_reports_the_effective_value_and_source(
    client: TestClient, monkeypatch: Any
) -> None:
    _force_env(monkeypatch, False)
    before = client.get("/api/v1/admin/deployment").json()
    assert (before["zero_sample_mode"], before["zero_sample_source"]) == (False, "off")
    client.put("/api/v1/admin/privacy", json={"zero_sample_mode": True})
    after = client.get("/api/v1/admin/deployment").json()
    assert (after["zero_sample_mode"], after["zero_sample_source"]) == (True, "db")
