"""Airflow webhook endpoint tests via TestClient against a real Postgres.

Auth is HMAC-SHA256 over the raw body in X-DataQ-Signature (ADR 0007), so the
test computes the signature over the exact bytes it sends. get_db +
get_secret_store are overridden; the store is seeded with the signing key.
Skips without TEST_DATABASE_URL.
"""

import hashlib
import hmac
import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.core.config import get_settings
from backend.app.core.secrets import SecretNotFoundError, get_secret_store
from backend.app.db.models import Connection, PipelineRun, Run, Suite, TriggerBinding, User
from backend.app.db.session import get_db
from backend.app.main import app

_SIGNING_KEY = "hmac-signing-key-abc"
_BASE_URL = "https://airflow.example.com"

_CALLBACK = {
    "dag_id": "load_finance",
    "run_id": "manual__2026-05-31",
    "state": "success",
    "base_url": _BASE_URL,
}

_URL = "/api/v1/orchestration/events/airflow"


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
    store.set(get_settings().airflow_webhook_secret_name, _SIGNING_KEY)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_secret_store] = lambda: store
    try:
        yield TestClient(app), store
    finally:
        app.dependency_overrides.clear()


def _sign(body: bytes, key: str = _SIGNING_KEY) -> str:
    return hmac.new(key.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _post(api: TestClient, body: bytes, signature: str | None) -> Any:
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["X-DataQ-Signature"] = signature
    return api.post(_URL, content=body, headers=headers)


def _seed_airflow_connection(db_session: Any, *, base_url: str = _BASE_URL) -> Connection:
    user = User(aad_object_id=uuid.uuid4().hex, email="dev@example.com")
    db_session.add(user)
    db_session.flush()
    conn = Connection(
        name="airflow-dev",
        type="airflow",
        env="dev",
        config={"base_url": base_url, "auth_type": "token"},
        created_by=user.id,
    )
    db_session.add(conn)
    db_session.commit()
    return conn


def test_valid_signed_event_records_run(
    client: tuple[TestClient, FakeStore], db_session: Any
) -> None:
    api, _ = client
    _seed_airflow_connection(db_session)
    body = json.dumps(_CALLBACK).encode()
    resp = _post(api, body, _sign(body))
    assert resp.status_code == 200
    assert resp.json() == {"status": "recorded", "triggered": 0}

    row = db_session.scalars(select(PipelineRun)).first()
    assert row is not None
    assert row.provider == "airflow"
    assert row.provider_run_id == "manual__2026-05-31"
    assert row.status == "succeeded"
    assert row.env == "dev"  # resolved via base_url


def test_succeeded_event_triggers_bound_suite(
    client: tuple[TestClient, FakeStore], db_session: Any
) -> None:
    api, _ = client
    conn = _seed_airflow_connection(db_session)
    suite = Suite(name="s1", connection_id=conn.id, created_by=conn.created_by)
    db_session.add(suite)
    db_session.commit()
    db_session.add(
        TriggerBinding(
            provider="airflow", pipeline_or_dag_id="load_finance", env="dev", suite_id=suite.id
        )
    )
    db_session.commit()

    body = json.dumps(_CALLBACK).encode()
    resp = _post(api, body, _sign(body))
    assert resp.json() == {"status": "recorded", "triggered": 1}
    run = db_session.scalars(select(Run)).one()
    assert run.triggered_by == "airflow:load_finance:manual__2026-05-31"


def test_invalid_signature_returns_401(client: tuple[TestClient, FakeStore]) -> None:
    api, _ = client
    body = json.dumps(_CALLBACK).encode()
    resp = _post(api, body, _sign(body, key="wrong-key"))
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "webhook_unauthorized"


def test_missing_signature_returns_401(client: tuple[TestClient, FakeStore]) -> None:
    api, _ = client
    resp = _post(api, json.dumps(_CALLBACK).encode(), None)
    assert resp.status_code == 401


def test_non_ascii_signature_is_clean_auth_error_not_typeerror() -> None:
    # ASGI decodes header bytes 0x80-0xFF as latin-1 -> a non-ASCII str, which
    # hmac.compare_digest rejects with TypeError. (httpx won't even send such a
    # header, so this asserts the auth fn directly.) The byte-compare must yield
    # a clean WebhookAuthError, not a TypeError → 500.
    from backend.app.api.v1.orchestration import WebhookAuthError, _authenticate_airflow

    store = FakeStore()
    store.set(get_settings().airflow_webhook_secret_name, _SIGNING_KEY)
    with pytest.raises(WebhookAuthError):
        _authenticate_airflow(b"{}", "sïgnatüre-ñ", store)


def test_tampered_body_fails_signature(client: tuple[TestClient, FakeStore]) -> None:
    api, _ = client
    body = json.dumps(_CALLBACK).encode()
    sig = _sign(body)
    tampered = json.dumps({**_CALLBACK, "dag_id": "evil"}).encode()
    resp = _post(api, tampered, sig)  # signature is for the original body
    assert resp.status_code == 401


def test_malformed_event_returns_422(client: tuple[TestClient, FakeStore]) -> None:
    api, _ = client
    bad = {k: v for k, v in _CALLBACK.items() if k != "run_id"}
    body = json.dumps(bad).encode()
    resp = _post(api, body, _sign(body))  # correctly signed, but missing run_id
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "orchestration_event_malformed"


def test_signing_key_not_configured_returns_503(client: tuple[TestClient, FakeStore]) -> None:
    api, store = client
    store.data.clear()  # signing key missing
    body = json.dumps(_CALLBACK).encode()
    resp = _post(api, body, _sign(body))
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "webhook_not_configured"
