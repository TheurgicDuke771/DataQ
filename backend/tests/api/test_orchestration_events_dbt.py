"""dbt webhook endpoint tests via TestClient against a real Postgres.

Auth is HMAC-SHA256 over the raw body in X-DataQ-Signature (ADR 0029), so the test
computes the signature over the exact bytes it sends. get_db + get_secret_store are
overridden; the store is seeded with the signing key. Skips without TEST_DATABASE_URL.
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

_SIGNING_KEY = "dbt-hmac-signing-key-abc"
_PROJECT = "dataq_lineage"

_CALLBACK = {
    "project_name": _PROJECT,
    "job_name": "lineage_build",
    "invocation_id": "522104cf-f67a-463f-bc5b-b6057cc93a62",
    "status": "success",
}

_URL = "/api/v1/orchestration/events/dbt"


class FakeStore:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, name: str) -> str:
        if name not in self.data:
            raise SecretNotFoundError(name)
        return self.data[name]

    def set(self, name: str, value: str) -> None:
        self.data[name] = value

    def delete(self, name: str) -> None:
        self.data.pop(name, None)


@pytest.fixture
def client(db_session: Any) -> Iterator[tuple[TestClient, FakeStore]]:
    store = FakeStore()
    store.set(get_settings().dbt_webhook_secret_name, _SIGNING_KEY)
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


def _seed_dbt_connection(db_session: Any, *, project: str = _PROJECT) -> Connection:
    user = User(aad_object_id=uuid.uuid4().hex, email="dev@example.com")
    db_session.add(user)
    db_session.flush()
    conn = Connection(
        name="dbt-dev",
        type="dbt",
        env="dev",
        config={
            "project_name": project,
            "artifacts_uri": "adls://acct/raw/dbt",
            "jobs": ["lineage_build"],
        },
        created_by=user.id,
    )
    db_session.add(conn)
    db_session.commit()
    return conn


def test_valid_signed_event_records_run(
    client: tuple[TestClient, FakeStore], db_session: Any
) -> None:
    api, _ = client
    _seed_dbt_connection(db_session)
    body = json.dumps(_CALLBACK).encode()
    resp = _post(api, body, _sign(body))
    assert resp.status_code == 200
    assert resp.json() == {"status": "recorded", "triggered": 0}

    row = db_session.scalars(select(PipelineRun)).first()
    assert row is not None
    assert row.provider == "dbt"
    assert row.provider_run_id == "522104cf-f67a-463f-bc5b-b6057cc93a62"
    assert row.status == "succeeded"
    assert row.env == "dev"  # resolved via project_name


def test_succeeded_event_triggers_bound_suite(
    client: tuple[TestClient, FakeStore], db_session: Any
) -> None:
    api, _ = client
    conn = _seed_dbt_connection(db_session)
    suite = Suite(name="s1", connection_id=conn.id, created_by=conn.created_by)
    db_session.add(suite)
    db_session.commit()
    db_session.add(
        TriggerBinding(
            provider="dbt", pipeline_or_dag_id="lineage_build", env="dev", suite_id=suite.id
        )
    )
    db_session.commit()

    body = json.dumps(_CALLBACK).encode()
    resp = _post(api, body, _sign(body))
    assert resp.json() == {"status": "recorded", "triggered": 1}
    run = db_session.scalars(select(Run)).one()
    assert run.triggered_by == "dbt:lineage_build:522104cf-f67a-463f-bc5b-b6057cc93a62"


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
    # A non-ASCII signature str must yield a clean WebhookAuthError, not a
    # TypeError → 500 (see the Airflow twin test for the ASGI/latin-1 rationale).
    from backend.app.api.v1.orchestration import WebhookAuthError, _authenticate_dbt

    store = FakeStore()
    store.set(get_settings().dbt_webhook_secret_name, _SIGNING_KEY)
    with pytest.raises(WebhookAuthError):
        _authenticate_dbt(b"{}", "sïgnatüre-ñ", store)


def test_tampered_body_fails_signature(client: tuple[TestClient, FakeStore]) -> None:
    api, _ = client
    body = json.dumps(_CALLBACK).encode()
    sig = _sign(body)
    tampered = json.dumps({**_CALLBACK, "job_name": "evil"}).encode()
    resp = _post(api, tampered, sig)  # signature is for the original body
    assert resp.status_code == 401


def test_malformed_event_returns_422(client: tuple[TestClient, FakeStore]) -> None:
    api, _ = client
    bad = {k: v for k, v in _CALLBACK.items() if k != "invocation_id"}
    body = json.dumps(bad).encode()
    resp = _post(api, body, _sign(body))  # correctly signed, but missing invocation_id
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "orchestration_event_malformed"


def test_signing_key_not_configured_returns_503(client: tuple[TestClient, FakeStore]) -> None:
    api, store = client
    store.data.clear()  # signing key missing
    body = json.dumps(_CALLBACK).encode()
    resp = _post(api, body, _sign(body))
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "webhook_not_configured"
