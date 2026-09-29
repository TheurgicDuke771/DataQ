"""`POST /api/v1/orchestration/gate` (ADR 0046): wiring, schema and a PAT caller."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.db.models import Connection, Share, Suite, TriggerBinding, User
from backend.app.db.session import get_db
from backend.app.main import app

_URL = "/api/v1/orchestration/gate"


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _bound_suite(db_session: Any) -> Suite:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"o-{uuid.uuid4().hex[:8]}@example.com")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "ab12345.eu-west-1"},
        secret_ref="kv-sf",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(name="s", connection_id=conn.id, created_by=owner.id, target={"table": "T"})
    db_session.add(suite)
    db_session.flush()
    db_session.add(
        TriggerBinding(
            provider="airflow", pipeline_or_dag_id="nightly", env="dev", suite_id=suite.id
        )
    )
    db_session.commit()
    return suite


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "provider": "airflow",
        "pipeline_or_dag_id": "nightly",
        "env": "dev",
        "provider_run_id": "scheduled__2026-09-29",
    }
    body.update(overrides)
    return body


def test_a_pat_caller_with_edit_starts_the_gate(
    client: TestClient,
    db_session: Any,
    as_role: Callable[..., tuple[Any, dict[str, str]]],
) -> None:
    suite = _bound_suite(db_session)
    user, headers = as_role("member")
    db_session.add(Share(suite_id=suite.id, user_id=user.id, permission="edit"))
    db_session.commit()

    resp = client.post(_URL, json=_body(), headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "running"
    assert body["created_runs"] == 1
    assert body["triggered_by"] == "airflow:nightly:scheduled__2026-09-29"
    assert body["suites"][0]["suite_id"] == str(suite.id)


def test_a_view_only_caller_can_poll_status_but_not_trigger(
    client: TestClient,
    db_session: Any,
    as_role: Callable[..., tuple[Any, dict[str, str]]],
) -> None:
    suite = _bound_suite(db_session)
    user, headers = as_role("member")
    db_session.add(Share(suite_id=suite.id, user_id=user.id, permission="view"))
    db_session.commit()

    status_only = client.post(_URL, json=_body(trigger=False), headers=headers)
    assert status_only.json()["state"] == "awaiting_trigger"
    refused = client.post(_URL, json=_body(), headers=headers)
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "gate_forbidden"


def test_a_pipeline_with_no_binding_is_not_found(client: TestClient) -> None:
    resp = client.post(_URL, json=_body(pipeline_or_dag_id="nothing_bound"))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "gate_binding_not_found"


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider": "snowflake"},
        {"fail_on": "pass"},
        {"env": "staging"},
        {"provider_run_id": ""},
        {"pipeline_or_dag_id": "x" * 257},
        {"max_severity": "fail"},
    ],
)
def test_a_malformed_request_is_refused_before_anything_runs(
    client: TestClient, overrides: dict[str, Any]
) -> None:
    resp = client.post(_URL, json=_body(**overrides))
    assert resp.status_code == 422
