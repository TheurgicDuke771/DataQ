"""Admin LLM config endpoints + the invocation read surface (ADR 0042)."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.core.secrets import get_secret_store
from backend.app.db.models import AuditEvent, User
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import llm_service
from backend.tests.support.fake_secret_store import FakeSecretStore


@pytest.fixture
def store() -> FakeSecretStore:
    return FakeSecretStore()


@pytest.fixture
def client(db_session: Any, store: FakeSecretStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_secret_store] = lambda: store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


_BODY = {
    "provider": "openai_compatible",
    "model": "qwen2.5:3b",
    "base_url": "http://ollama.local/v1",
    "api_key": "sk-1",
    "structured_output": "prompt_json",
    "enabled": True,
}


def test_get_unconfigured_reads_configured_false(client: TestClient) -> None:
    resp = client.get("/api/v1/admin/llm")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is False
    assert body["enabled"] is False


def test_put_then_get_round_trip_never_returns_the_key(
    client: TestClient, db_session: Any, store: FakeSecretStore
) -> None:
    resp = client.put("/api/v1/admin/llm", json=_BODY)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["configured"] is True
    assert body["has_credential"] is True
    assert "sk-1" not in resp.text
    assert "api_key_secret_ref" not in body
    read = client.get("/api/v1/admin/llm")
    assert "sk-1" not in read.text
    event = db_session.query(AuditEvent).filter(AuditEvent.action == "llm_setting.update").one()
    assert "sk-1" not in str(event.after)


def test_put_destination_move_without_key_is_422(client: TestClient) -> None:
    assert client.put("/api/v1/admin/llm", json=_BODY).status_code == 200
    moved = {**_BODY, "base_url": "http://evil.example/v1"}
    moved.pop("api_key")
    resp = client.put("/api/v1/admin/llm", json=moved)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "llm_config_invalid"


def test_unknown_field_is_422(client: TestClient) -> None:
    resp = client.put("/api/v1/admin/llm", json={**_BODY, "target_override": "x"})
    assert resp.status_code == 422


def test_non_admin_is_403_on_all_admin_llm_routes(
    client: TestClient, as_role: Callable[..., tuple[Any, dict[str, str]]]
) -> None:
    _, headers = as_role("member")
    assert client.get("/api/v1/admin/llm", headers=headers).status_code == 403
    assert client.put("/api/v1/admin/llm", json=_BODY, headers=headers).status_code == 403
    assert client.post("/api/v1/admin/llm/test", json=_BODY, headers=headers).status_code == 403


def test_llm_test_endpoint_reports_without_persisting(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        llm_service,
        "test_settings",
        lambda _db, *, draft, secret_store: {"ok": True, "model": draft.model, "latency_ms": 5},
    )
    resp = client.post("/api/v1/admin/llm/test", json=_BODY)
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert llm_service.get_settings_row(db_session) is None


def test_posture_llm_row_flips_with_config(client: TestClient) -> None:
    def _llm_row(payload: dict[str, Any]) -> dict[str, Any]:
        return next(t for t in payload["external_transfers"] if t["name"] == "llm_intelligence")

    before = _llm_row(client.get("/api/v1/admin/deployment").json())
    assert before["enabled"] is False
    assert client.put("/api/v1/admin/llm", json=_BODY).status_code == 200
    after = _llm_row(client.get("/api/v1/admin/deployment").json())
    assert after["enabled"] is True
    assert "qwen2.5:3b" in after["detail"]


# ── invocation read surface ──────────────────────────────────────────────────


def _invocation(db_session: Any, requested_by: User) -> Any:
    from backend.app.db.models import LlmInvocation

    invocation = LlmInvocation(kind="ping", requested_by_user_id=requested_by.id)
    db_session.add(invocation)
    db_session.commit()
    return invocation


def test_invocation_visible_to_requester_and_admin_404_others(
    client: TestClient,
    db_session: Any,
    as_role: Callable[..., tuple[Any, dict[str, str]]],
) -> None:
    requester, requester_headers = as_role("member")
    _, other_headers = as_role("member")
    _, admin_headers = as_role("admin")
    invocation = _invocation(db_session, requester)
    url = f"/api/v1/llm/invocations/{invocation.id}"
    assert client.get(url, headers=requester_headers).status_code == 200
    assert client.get(url, headers=admin_headers).status_code == 200
    resp = client.get(url, headers=other_headers)
    assert resp.status_code == 404  # no-leak: someone else's invocation reads as absent
    assert client.get(f"/api/v1/llm/invocations/{uuid.uuid4()}").status_code == 404
