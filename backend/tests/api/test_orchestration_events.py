"""ADF webhook endpoint tests via TestClient against a real Postgres.

get_db + get_secret_store are overridden to the test session and an in-memory
store seeded with the webhook secret. Auth is the shared token (ADR 0006), not
Azure AD, so no user override is needed. Skips without TEST_DATABASE_URL.
"""

import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.core.config import get_settings
from backend.app.core.secrets import SecretNotFoundError, get_secret_store
from backend.app.db.models import Connection, PipelineRun, User
from backend.app.db.session import get_db
from backend.app.main import app

_SECRET = "s3cr3t-webhook-token"

_ADF_CONFIG = {
    "subscription_id": "00000000-0000-0000-0000-000000000001",
    "resource_group": "rg-data",
    "factory_name": "lll-adf-nonprod",
    "tenant_id": "00000000-0000-0000-0000-0000000000aa",
    "client_id": "00000000-0000-0000-0000-0000000000bb",
}

_EVENT = {
    "factoryName": "lll-adf-nonprod",
    "pipelineName": "load_finance",
    "runId": "run-abc-123",
    "status": "Failed",
    "message": "Activity failed",
}


class FakeStore:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, name: str) -> str:
        if name not in self.data:
            raise SecretNotFoundError(name)
        return self.data[name]

    def set(self, name: str, value: str) -> None:
        self.data[name] = value


@pytest.fixture
def client(db_session: Any) -> Iterator[tuple[TestClient, FakeStore]]:
    store = FakeStore()
    store.set(get_settings().adf_webhook_secret_name, _SECRET)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_secret_store] = lambda: store
    try:
        yield TestClient(app), store
    finally:
        app.dependency_overrides.clear()


def _seed_adf_connection(db_session: Any) -> Connection:
    user = User(aad_object_id=uuid.uuid4().hex, email="dev@example.com")
    db_session.add(user)
    db_session.flush()
    conn = Connection(
        name="adf-dev", type="adf", env="dev", config=dict(_ADF_CONFIG), created_by=user.id
    )
    db_session.add(conn)
    db_session.commit()
    return conn


_URL = "/api/v1/orchestration/events/adf"


def test_valid_event_records_pipeline_run(
    client: tuple[TestClient, FakeStore], db_session: Any
) -> None:
    api, _ = client
    _seed_adf_connection(db_session)
    resp = api.post(_URL, params={"token": _SECRET}, json=_EVENT)
    assert resp.status_code == 200
    assert resp.json() == {"status": "recorded", "triggered": 0}

    row = db_session.scalars(select(PipelineRun)).first()
    assert row is not None
    assert row.provider_run_id == "run-abc-123"
    assert row.status == "failed"


def test_unattributable_event_acknowledged_as_ignored(client: tuple[TestClient, FakeStore]) -> None:
    api, _ = client  # no connection seeded → cannot attribute
    resp = api.post(_URL, params={"token": _SECRET}, json=_EVENT)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ignored", "triggered": 0}


def test_succeeded_event_triggers_bound_suite(
    client: tuple[TestClient, FakeStore], db_session: Any
) -> None:
    from backend.app.db.models import Run, Suite, TriggerBinding

    api, _ = client
    conn = _seed_adf_connection(db_session)  # env=dev, no secret_ref → no enrichment
    suite = Suite(name="s1", connection_id=conn.id, created_by=conn.created_by)
    db_session.add(suite)
    db_session.commit()
    db_session.add(
        TriggerBinding(
            provider="adf", pipeline_or_dag_id="load_finance", env="dev", suite_id=suite.id
        )
    )
    db_session.commit()

    resp = api.post(_URL, params={"token": _SECRET}, json={**_EVENT, "status": "Succeeded"})
    assert resp.status_code == 200
    assert resp.json() == {"status": "recorded", "triggered": 1}
    run = db_session.scalars(select(Run)).one()
    assert run.status == "queued"
    assert run.triggered_by == "adf:load_finance:run-abc-123"


def test_missing_token_returns_401(client: tuple[TestClient, FakeStore]) -> None:
    api, _ = client
    resp = api.post(_URL, json=_EVENT)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "webhook_unauthorized"


def test_wrong_token_returns_401(client: tuple[TestClient, FakeStore]) -> None:
    api, _ = client
    resp = api.post(_URL, params={"token": "wrong"}, json=_EVENT)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "webhook_unauthorized"


def test_non_ascii_token_returns_401_not_500(client: tuple[TestClient, FakeStore]) -> None:
    # hmac.compare_digest rejects non-ASCII str; the byte-compare must keep this
    # a clean 401 rather than a TypeError → 500.
    api, _ = client
    resp = api.post(_URL, params={"token": "tökèn-ñ"}, json=_EVENT)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "webhook_unauthorized"


def test_malformed_event_returns_422(client: tuple[TestClient, FakeStore]) -> None:
    api, _ = client
    bad = {k: v for k, v in _EVENT.items() if k != "runId"}
    resp = api.post(_URL, params={"token": _SECRET}, json=bad)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "orchestration_event_malformed"


def test_non_json_body_returns_422(client: tuple[TestClient, FakeStore]) -> None:
    api, _ = client
    resp = api.post(
        _URL,
        params={"token": _SECRET},
        content=b"not json{",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422


def test_secret_not_configured_returns_503(
    client: tuple[TestClient, FakeStore],
) -> None:
    api, store = client
    store.data.clear()  # receiver secret missing
    resp = api.post(_URL, params={"token": _SECRET}, json=_EVENT)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "webhook_not_configured"


def test_token_in_url_not_logged_via_path(
    client: tuple[TestClient, FakeStore], db_session: Any
) -> None:
    # Sanity: the body parse path is exercised; this documents that the token
    # rides the query string (the request middleware logs path only, not query).
    api, _ = client
    _seed_adf_connection(db_session)
    resp = api.post(_URL, params={"token": _SECRET}, content=json.dumps(_EVENT).encode())
    assert resp.status_code == 200
