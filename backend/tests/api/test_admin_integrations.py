"""Admin Integrations — webhook regeneration, poll-now, inventory sync (#1701)."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from structlog.testing import capture_logs

from backend.app.core.config import get_settings
from backend.app.db.models import AuditEvent, Connection, ConnectionVersion, User
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import webhook_secret_service
from backend.tests.support.fake_secret_store import FakeSecretStore, override_secret_store


@pytest.fixture
def store() -> FakeSecretStore:
    settings = get_settings()
    return FakeSecretStore(
        {
            settings.adf_webhook_secret_name: "adf-current",
            settings.airflow_webhook_secret_name: "airflow-current",
        }
    )


@pytest.fixture
def client(db_session: Any, store: FakeSecretStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    override_secret_store(app, store)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _user(db_session: Any, role: str = "member") -> User:
    user = User(
        id=uuid.uuid4(),
        aad_object_id=None,
        email=f"{role}-{uuid.uuid4().hex[:8]}@example.com",
        role=role,
    )
    db_session.add(user)
    db_session.commit()
    return user


def _orchestration_connection(db_session: Any, provider: str = "adf") -> Connection:
    config = (
        {"subscription_id": str(uuid.uuid4()), "resource_group": "rg", "factory_name": "f1"}
        if provider == "adf"
        else {"base_url": "https://airflow.example.com"}
    )
    conn = Connection(
        name=f"{provider}-{uuid.uuid4().hex[:8]}",
        type=provider,
        env="dev",
        config=config,
        secret_ref="kv-orch",
        created_by=_user(db_session, "admin").id,
    )
    db_session.add(conn)
    db_session.commit()
    return conn


def _warehouse_connection(db_session: Any, *, inventory_sync: bool = False) -> Connection:
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={
            "account": "ab12345.eu-west-1",
            "user": "svc",
            "database": "DB",
            "schema": "PUBLIC",
            "warehouse": "WH",
            "role": "ANALYST",
            "inventory_sync": inventory_sync,
        },
        secret_ref="kv-sf",
        created_by=_user(db_session, "admin").id,
    )
    db_session.add(conn)
    db_session.commit()
    return conn


# ── regeneration ─────────────────────────────────────────────────────────────


def test_regenerate_returns_the_new_value_once_and_stores_it(
    client: TestClient, store: FakeSecretStore
) -> None:
    settings = get_settings()

    body = client.post("/api/v1/admin/orchestration/webhooks/adf/regenerate").json()

    assert body["value"] == store.data[settings.adf_webhook_secret_name]
    assert body["value"] != "adf-current"
    assert body["auth_mode"] == "url_token"
    # The URL is the paste target for a URL-token provider, and carries the new value.
    assert body["value"] in body["inbound_url"].replace("%3D", "=")
    assert body["grace_until"] is not None
    # No read endpoint hands the value back a second time.
    listed = client.get("/api/v1/admin/orchestration/webhooks").json()
    assert all(body["value"] not in str(row) for row in listed) or listed == []


def test_regenerate_parks_the_previous_value_with_an_expiry(
    client: TestClient, store: FakeSecretStore
) -> None:
    settings = get_settings()
    previous_key = webhook_secret_service.previous_key_name(settings.adf_webhook_secret_name)

    client.post("/api/v1/admin/orchestration/webhooks/adf/regenerate")

    parked = store.data[previous_key]
    assert "adf-current" in parked
    grace = settings.webhook_secret_grace_minutes
    expires = datetime.fromisoformat(parked.split('"expires_at": "')[1].split('"')[0])
    assert expires - datetime.now(UTC) <= timedelta(minutes=grace)


def test_the_previous_value_still_authenticates_inside_the_grace_window(
    client: TestClient,
) -> None:
    """The whole point of the window: the provider side has not been updated yet."""
    client.post("/api/v1/admin/orchestration/webhooks/adf/regenerate")

    resp = client.post("/api/v1/orchestration/events/adf?token=adf-current", json={})

    # Not 401: auth passed and the body was rejected instead.
    assert resp.status_code != 401, resp.text


def test_the_previous_value_is_refused_once_the_window_closes(
    client: TestClient, store: FakeSecretStore
) -> None:
    settings = get_settings()
    previous_key = webhook_secret_service.previous_key_name(settings.adf_webhook_secret_name)
    client.post("/api/v1/admin/orchestration/webhooks/adf/regenerate")
    # Freeze the parked value's expiry into the past — the same blob, one field moved.
    expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    store.data[previous_key] = f'{{"value": "adf-current", "expires_at": "{expired}"}}'

    resp = client.post("/api/v1/orchestration/events/adf?token=adf-current", json={})

    assert resp.status_code == 401


def test_the_new_value_authenticates_immediately(client: TestClient) -> None:
    new_value = client.post("/api/v1/admin/orchestration/webhooks/adf/regenerate").json()["value"]

    resp = client.post("/api/v1/orchestration/events/adf", params={"token": new_value}, json={})

    assert resp.status_code != 401, resp.text


def test_an_hmac_provider_rotates_the_signing_key(client: TestClient) -> None:
    body = client.post("/api/v1/admin/orchestration/webhooks/airflow/regenerate").json()
    assert body["auth_mode"] == "hmac"
    assert body["inbound_url"] is None

    payload = b"{}"
    for key in (body["value"], "airflow-current"):
        signature = hmac.new(key.encode(), payload, hashlib.sha256).hexdigest()
        resp = client.post(
            "/api/v1/orchestration/events/airflow",
            content=payload,
            headers={"X-DataQ-Signature": signature, "Content-Type": "application/json"},
        )
        assert resp.status_code != 401, f"{key} was rejected: {resp.text}"


def test_a_stale_signature_is_refused_after_the_window(
    client: TestClient, store: FakeSecretStore
) -> None:
    settings = get_settings()
    previous_key = webhook_secret_service.previous_key_name(settings.airflow_webhook_secret_name)
    client.post("/api/v1/admin/orchestration/webhooks/airflow/regenerate")
    expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    store.data[previous_key] = f'{{"value": "airflow-current", "expires_at": "{expired}"}}'

    payload = b"{}"
    signature = hmac.new(b"airflow-current", payload, hashlib.sha256).hexdigest()
    resp = client.post(
        "/api/v1/orchestration/events/airflow",
        content=payload,
        headers={"X-DataQ-Signature": signature, "Content-Type": "application/json"},
    )

    assert resp.status_code == 401


def test_regenerate_never_writes_the_value_to_an_audit_row_or_a_log(
    client: TestClient, db_session: Any
) -> None:
    with capture_logs() as logs:
        value = client.post("/api/v1/admin/orchestration/webhooks/adf/regenerate").json()["value"]

    event = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "orchestration_webhook.regenerate")
    ).one()
    assert event.after["provider"] == "adf"
    assert event.after["grace_until"] is not None
    assert value not in str(event.after)
    assert all(value not in str(line) for line in logs)


def test_regenerate_rejects_an_unknown_provider(client: TestClient) -> None:
    resp = client.post("/api/v1/admin/orchestration/webhooks/snowflake/regenerate")
    assert resp.status_code == 404


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_regenerate_is_admin_only(client: TestClient, as_role: Any, role: str) -> None:
    _, headers = as_role(role)
    resp = client.post("/api/v1/admin/orchestration/webhooks/adf/regenerate", headers=headers)
    assert resp.status_code == 403


# ── poll now ─────────────────────────────────────────────────────────────────


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture Celery dispatch — only the broker call is mocked, nothing else."""
    calls: list[dict[str, Any]] = []

    class _Result:
        id = "task-123"

    def _send_task(name: str, **kwargs: Any) -> _Result:
        calls.append({"name": name, **kwargs})
        return _Result()

    from backend.app.worker import celery_app as celery_module

    monkeypatch.setattr(celery_module.celery_app, "send_task", _send_task)
    return calls


def test_poll_now_enqueues_one_sweep_per_provider(
    client: TestClient, db_session: Any, sent: list[dict[str, Any]]
) -> None:
    _orchestration_connection(db_session, "adf")
    _orchestration_connection(db_session, "airflow")

    resp = client.post("/api/v1/admin/orchestration/poll-now")

    assert resp.status_code == 202, resp.text
    providers = {row["provider"] for row in resp.json()["dispatched"]}
    assert providers == {"adf", "airflow"}
    assert [call["name"] for call in sent] == ["poll_orchestration_runs"] * 2


def test_poll_now_for_one_connection_narrows_to_its_resource(
    client: TestClient, db_session: Any, sent: list[dict[str, Any]]
) -> None:
    conn = _orchestration_connection(db_session, "adf")

    body = client.post(f"/api/v1/admin/orchestration/poll-now?connection_id={conn.id}").json()

    assert body["dispatched"][0]["scope"] == "connection"
    assert sent[0]["kwargs"]["resource_name"] == "f1"


def test_poll_now_says_provider_scope_when_the_connection_names_no_resource(
    client: TestClient, db_session: Any, sent: list[dict[str, Any]]
) -> None:
    """A connection with no resource key sweeps the whole provider — calling that
    "connection" would claim a narrowing the poll does not apply.
    """
    conn = _orchestration_connection(db_session, "adf")
    conn.config = {**conn.config, "factory_name": ""}
    db_session.commit()

    body = client.post(f"/api/v1/admin/orchestration/poll-now?connection_id={conn.id}").json()

    assert body["dispatched"][0]["scope"] == "provider"


def test_poll_now_is_audited(
    client: TestClient, db_session: Any, sent: list[dict[str, Any]]
) -> None:
    _orchestration_connection(db_session, "adf")

    client.post("/api/v1/admin/orchestration/poll-now")

    event = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "orchestration_poll.request")
    ).one()
    assert event.after["dispatched"] == ["task-123"]


def test_poll_now_404s_on_a_datasource_connection(
    client: TestClient, db_session: Any, sent: list[dict[str, Any]]
) -> None:
    conn = _warehouse_connection(db_session)
    resp = client.post(f"/api/v1/admin/orchestration/poll-now?connection_id={conn.id}")
    assert resp.status_code == 404
    assert sent == []


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_poll_now_is_admin_only(
    client: TestClient, as_role: Any, role: str, sent: list[dict[str, Any]]
) -> None:
    _, headers = as_role(role)
    resp = client.post("/api/v1/admin/orchestration/poll-now", headers=headers)
    assert resp.status_code == 403
    assert sent == []


# ── inventory sync ───────────────────────────────────────────────────────────


def test_a_never_synced_connection_reports_unknown_not_zero(
    client: TestClient, db_session: Any
) -> None:
    conn = _warehouse_connection(db_session)

    row = next(
        r
        for r in client.get("/api/v1/admin/inventory-sync").json()
        if r["connection_id"] == str(conn.id)
    )

    assert row["status"] == "never_synced"
    assert row["tables_discovered"] is None
    assert row["unmonitored"] is None


def test_a_synced_connection_reports_its_counts(client: TestClient, db_session: Any) -> None:
    conn = _warehouse_connection(db_session, inventory_sync=True)
    conn.inventory_sync_last_attempted_at = datetime.now(UTC)
    conn.inventory_sync_last_table_count = 7
    db_session.commit()

    row = next(
        r
        for r in client.get("/api/v1/admin/inventory-sync").json()
        if r["connection_id"] == str(conn.id)
    )

    assert row["status"] == "synced"
    assert row["enabled"] is True
    assert row["tables_discovered"] == 7
    assert row["unmonitored"] == 0


def test_a_failing_connection_carries_its_classified_reason(
    client: TestClient, db_session: Any
) -> None:
    conn = _warehouse_connection(db_session, inventory_sync=True)
    conn.inventory_sync_last_attempted_at = datetime.now(UTC)
    conn.inventory_sync_last_error = "authentication failed"
    conn.inventory_sync_failing_since = datetime.now(UTC)
    db_session.commit()

    row = next(
        r
        for r in client.get("/api/v1/admin/inventory-sync").json()
        if r["connection_id"] == str(conn.id)
    )

    assert row["status"] == "failing"
    assert row["last_error"] == "authentication failed"


def test_the_toggle_goes_through_the_connection_update_path(
    client: TestClient, db_session: Any
) -> None:
    """A direct JSONB write would skip both the audit row and the version snapshot."""
    conn = _warehouse_connection(db_session)

    resp = client.patch(f"/api/v1/admin/inventory-sync/{conn.id}", json={"enabled": True})

    assert resp.status_code == 200, resp.text
    assert resp.json()["enabled"] is True
    db_session.refresh(conn)
    assert conn.config["inventory_sync"] is True
    versions = db_session.scalars(
        select(ConnectionVersion).where(ConnectionVersion.connection_id == conn.id)
    ).all()
    assert versions, "the update must snapshot a connection version"
    assert db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "connection.update")
    ).all()


def test_turning_the_toggle_off_clears_the_sync_bookkeeping(
    client: TestClient, db_session: Any
) -> None:
    conn = _warehouse_connection(db_session, inventory_sync=True)
    conn.inventory_sync_last_attempted_at = datetime.now(UTC)
    conn.inventory_sync_last_table_count = 3
    db_session.commit()

    body = client.patch(f"/api/v1/admin/inventory-sync/{conn.id}", json={"enabled": False}).json()

    assert body["enabled"] is False
    assert body["status"] == "never_synced"
    assert body["tables_discovered"] is None


def test_the_toggle_404s_on_a_type_with_no_enumerator(client: TestClient, db_session: Any) -> None:
    conn = _orchestration_connection(db_session, "adf")
    resp = client.patch(f"/api/v1/admin/inventory-sync/{conn.id}", json={"enabled": True})
    assert resp.status_code == 404


def test_run_now_enqueues_the_sync_task(
    client: TestClient, db_session: Any, sent: list[dict[str, Any]]
) -> None:
    conn = _warehouse_connection(db_session)

    resp = client.post(f"/api/v1/admin/inventory-sync/{conn.id}/run")

    assert resp.status_code == 202, resp.text
    assert resp.json()["task_id"] == "task-123"
    assert sent[0]["name"] == "sync_connection_asset_inventory"
    assert sent[0]["kwargs"]["connection_id"] == str(conn.id)
    assert db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "inventory_sync.run")
    ).one()


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_the_inventory_routes_are_admin_only(
    client: TestClient, db_session: Any, as_role: Any, role: str, sent: list[dict[str, Any]]
) -> None:
    conn = _warehouse_connection(db_session)
    _, headers = as_role(role)

    assert client.get("/api/v1/admin/inventory-sync", headers=headers).status_code == 403
    assert (
        client.patch(
            f"/api/v1/admin/inventory-sync/{conn.id}", json={"enabled": True}, headers=headers
        ).status_code
        == 403
    )
    assert (
        client.post(f"/api/v1/admin/inventory-sync/{conn.id}/run", headers=headers).status_code
        == 403
    )
    db_session.refresh(conn)
    assert conn.config["inventory_sync"] is False, "a 403 must not have written anything"
    assert sent == []
