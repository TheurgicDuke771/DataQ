"""`/admin/telemetry`, `/telemetry/events` and the posture disclosure (ADR 0050)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.core import rate_limit, usage_telemetry
from backend.app.core.config import get_settings
from backend.app.db.models import AuditEvent
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import telemetry_settings_service as svc

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    usage_telemetry.reset()
    svc.invalidate()
    yield
    usage_telemetry.reset()
    svc.invalidate()


@pytest.fixture
def capture() -> Any:
    from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    logs = InMemoryLogRecordExporter()
    assert usage_telemetry.configure(
        "test", log_exporters=[logs], metric_readers=[InMemoryMetricReader()]
    )
    return logs


def _sink(monkeypatch: pytest.MonkeyPatch, sink: str, tmp_path: Any = None) -> None:
    monkeypatch.setenv("TELEMETRY_SINK", sink)
    if sink == "file":
        monkeypatch.setenv("TELEMETRY_FILE_PATH", str(tmp_path / "usage.jsonl"))
    get_settings.cache_clear()


def _names(logs: Any) -> list[str]:
    return [r.log_record.event_name for r in logs.get_finished_logs()]


# ───────────────────────── admin settings ─────────────────────────


def test_default_is_off_and_lists_the_vocabulary(client: TestClient) -> None:
    body = client.get("/api/v1/admin/telemetry").json()
    assert (body["enabled"], body["stored"], body["sink"]) == (False, False, "off")
    assert {e["name"] for e in body["events"]} == {e.value for e in usage_telemetry.UsageEvent}
    assert body["updated_by"] is None


def test_opt_in_without_a_sink_is_stored_but_not_effective(
    client: TestClient, db_session: Any
) -> None:
    body = client.put("/api/v1/admin/telemetry", json={"enabled": True}).json()
    assert (body["enabled"], body["stored"]) == (False, True)
    events = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "telemetry_setting.update")
    ).all()
    assert len(events) == 1
    assert events[0].before is None and events[0].after["enabled"] is True


def test_opt_in_with_a_sink_is_effective(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    _sink(monkeypatch, "file", tmp_path)
    body = client.put("/api/v1/admin/telemetry", json={"enabled": True}).json()
    assert (body["enabled"], body["sink"]) == (True, "file")
    assert body["updated_by"] and body["updated_at"]
    off = client.put("/api/v1/admin/telemetry", json={"enabled": False}).json()
    assert off["enabled"] is False


def test_the_write_is_strict(client: TestClient) -> None:
    coerced = client.put("/api/v1/admin/telemetry", json={"enabled": "yes"})
    extra = client.put("/api/v1/admin/telemetry", json={"enabled": True, "x": 1})
    assert (coerced.status_code, extra.status_code) == (422, 422)


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_non_admins_are_refused(
    client: TestClient, as_role: Callable[..., tuple[Any, dict[str, str]]], role: str
) -> None:
    _, headers = as_role(role)
    got = client.get("/api/v1/admin/telemetry", headers=headers)
    put = client.put("/api/v1/admin/telemetry", json={"enabled": True}, headers=headers)
    assert (got.status_code, put.status_code) == (403, 403)


# ───────────────────────── posture ─────────────────────────


def _usage_row(client: TestClient) -> dict[str, Any]:
    transfers = client.get("/api/v1/admin/deployment").json()["external_transfers"]
    (row,) = [t for t in transfers if t["name"] == "usage_telemetry"]
    return dict(row)


def test_posture_discloses_the_setting_in_every_state(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    off = _usage_row(client)
    assert off["enabled"] is False and "has not opted in" in off["detail"]

    client.put("/api/v1/admin/telemetry", json={"enabled": True})
    no_sink = _usage_row(client)
    assert no_sink["enabled"] is False and "TELEMETRY_SINK is off" in no_sink["detail"]

    _sink(monkeypatch, "file", tmp_path)
    on = _usage_row(client)
    assert on["enabled"] is True
    assert str(tmp_path / "usage.jsonl") in on["detail"]


# ───────────────────────── browser events ─────────────────────────


def test_client_events_are_emitted_only_with_both_gates(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Any, capture: Any
) -> None:
    body = {"events": [{"name": "page_viewed", "attributes": {"route": "suites"}}]}
    first = client.post("/api/v1/telemetry/events", json=body)
    assert first.status_code == 202 and first.json() == {"accepted": 1, "enabled": False}
    assert _names(capture) == []

    _sink(monkeypatch, "file", tmp_path)
    client.put("/api/v1/admin/telemetry", json={"enabled": True})
    capture.clear()
    second = client.post("/api/v1/telemetry/events", json=body)
    assert second.json() == {"accepted": 1, "enabled": True}
    assert _names(capture) == ["dataq.usage.page_viewed"]
    status = client.get("/api/v1/telemetry")
    assert status.json() == {
        "enabled": True,
        "events": ["check_editor_abandoned", "page_viewed"],
    }


@pytest.mark.parametrize(
    "event",
    [
        {"name": "page_viewed", "attributes": {"route": "/suites/1234"}},
        {"name": "page_viewed", "attributes": {"route": "suites", "table": "orders"}},
        {"name": "check_created", "attributes": {"kind": "freshness"}},
        {"name": "table_scanned", "attributes": {}},
        {"name": "check_editor_abandoned", "attributes": {"step": 3}},
    ],
)
def test_client_events_outside_the_vocabulary_are_refused(
    client: TestClient, event: dict[str, Any], capture: Any
) -> None:
    resp = client.post("/api/v1/telemetry/events", json={"events": [event]})
    assert resp.status_code == 422
    assert "orders" not in resp.text and "/suites/1234" not in resp.text
    assert _names(capture) == []


def test_one_bad_event_refuses_the_whole_batch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Any, capture: Any
) -> None:
    _sink(monkeypatch, "file", tmp_path)
    client.put("/api/v1/admin/telemetry", json={"enabled": True})
    capture.clear()
    resp = client.post(
        "/api/v1/telemetry/events",
        json={
            "events": [
                {"name": "page_viewed", "attributes": {"route": "suites"}},
                {"name": "page_viewed", "attributes": {"route": "SELECT 1"}},
            ]
        },
    )
    assert resp.status_code == 422
    assert _names(capture) == []


def test_a_batch_is_bounded(client: TestClient) -> None:
    event = {"name": "page_viewed", "attributes": {"route": "suites"}}
    too_many = client.post("/api/v1/telemetry/events", json={"events": [event] * 21})
    none = client.post("/api/v1/telemetry/events", json={"events": []})
    assert (too_many.status_code, none.status_code) == (422, 422)


def test_the_route_needs_a_signed_in_user(
    client: TestClient, as_role: Callable[..., tuple[Any, dict[str, str]]]
) -> None:
    _, headers = as_role("viewer")
    ok = client.post(
        "/api/v1/telemetry/events",
        json={"events": [{"name": "page_viewed", "attributes": {"route": "dashboard"}}]},
        headers=headers,
    )
    assert ok.status_code == 202
    bad = client.post(
        "/api/v1/telemetry/events",
        json={"events": [{"name": "page_viewed", "attributes": {"route": "dashboard"}}]},
        headers={"Authorization": "Bearer dq_live_not-a-real-token"},
    )
    assert bad.status_code == 401


def test_the_route_has_its_own_rate_limit_class() -> None:
    settings = get_settings()
    cls, limit, key = rate_limit._resolve_policy(
        "/api/v1/telemetry/events", "POST", "dq_live_x", "10.0.0.0/24", settings
    )
    assert (cls, limit) == ("telemetry", settings.rate_limit_telemetry_per_minute)
    assert key.startswith("tok:")
    read, _, _ = rate_limit._resolve_policy(
        "/api/v1/telemetry", "GET", "dq_live_x", "10.0.0.0/24", settings
    )
    assert read == "default"


# ───────────────────────── server-side emit sites ─────────────────────────


def test_me_emits_the_sign_in_event_with_the_auth_kind(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    capture: Any,
    as_role: Callable[..., tuple[Any, dict[str, str]]],
) -> None:
    _sink(monkeypatch, "file", tmp_path)
    client.put("/api/v1/admin/telemetry", json={"enabled": True})
    capture.clear()
    _, headers = as_role("member")
    client.get("/api/v1/me", headers=headers)
    (record,) = capture.get_finished_logs()
    assert record.log_record.event_name == "dataq.usage.user_signed_in"
    assert dict(record.log_record.attributes) == {"event": "user_signed_in", "auth_mode": "pat"}
