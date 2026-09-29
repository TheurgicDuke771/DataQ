"""The user-facing Airflow gate snippet against the real gate endpoint (ADR 0046)."""

from __future__ import annotations

import importlib.util
import io
import json
import urllib.request
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.db.models import Check, Connection, Result, Run, Suite, TriggerBinding
from backend.app.db.session import get_db
from backend.app.main import app


def _load_snippet() -> Any:
    path = Path(__file__).resolve().parents[3] / "integrations" / "airflow" / "dataq_gate.py"
    spec = importlib.util.spec_from_file_location("dataq_gate", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


snippet = _load_snippet()


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def wired(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> list[dict[str, Any]]:
    """The snippet's HTTP call routed into the app; returns the bodies it sent."""
    sent: list[dict[str, Any]] = []

    def _urlopen(request: urllib.request.Request, timeout: float) -> Any:
        assert request.full_url == "https://dq.example.com/api/v1/orchestration/gate"
        assert isinstance(request.data, bytes)
        body = json.loads(request.data)
        sent.append(body)
        resp = client.post("/api/v1/orchestration/gate", json=body)
        assert resp.status_code == 200, resp.text
        return io.BytesIO(resp.content)

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    monkeypatch.setenv("DATAQ_URL", "https://dq.example.com/")
    monkeypatch.setenv("DATAQ_PAT", "dq_live_unused-under-dev-bypass")
    monkeypatch.setenv("DATAQ_ENV", "dev")
    return sent


def _bound_suite(client: TestClient, db_session: Any) -> uuid.UUID:
    # Owned by the dev-bypass caller, so it has edit.
    me = uuid.UUID(client.get("/api/v1/me").json()["id"])
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "ab12345.eu-west-1"},
        secret_ref="kv-sf",
        created_by=me,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(name="gated", connection_id=conn.id, created_by=me, target={"table": "T"})
    db_session.add(suite)
    db_session.flush()
    db_session.add(
        TriggerBinding(
            provider="airflow", pipeline_or_dag_id="load_finance", env="dev", suite_id=suite.id
        )
    )
    db_session.commit()
    return suite.id


def _context() -> dict[str, Any]:
    return {"dag": SimpleNamespace(dag_id="load_finance"), "run_id": "scheduled__2026-09-29"}


def _finish(db_session: Any, status: str) -> None:
    run = db_session.scalars(select(Run)).one()
    run.status = "succeeded"
    check = Check(
        suite_id=run.suite_id,
        name="c",
        expectation_type="expect_column_values_to_not_be_null",
        config={"column": "id"},
    )
    db_session.add(check)
    db_session.flush()
    db_session.add(Result(run_id=run.id, check_id=check.id, status=status))
    db_session.commit()


def test_a_poke_starts_the_run_and_waits(
    client: TestClient, db_session: Any, wired: list[dict[str, Any]]
) -> None:
    _bound_suite(client, db_session)

    assert snippet.dataq_gate(**_context()) is False
    assert wired[0] == {
        "provider": "airflow",
        "pipeline_or_dag_id": "load_finance",
        "env": "dev",
        "provider_run_id": "scheduled__2026-09-29",
        "fail_on": "fail",
        "trigger": True,
    }
    assert db_session.scalars(select(Run)).one().triggered_by == (
        "airflow:load_finance:scheduled__2026-09-29"
    )


def test_a_passed_gate_lets_the_dag_continue(
    client: TestClient, db_session: Any, wired: list[dict[str, Any]]
) -> None:
    _bound_suite(client, db_session)
    snippet.dataq_gate(**_context())
    _finish(db_session, "pass")

    assert snippet.dataq_gate(**_context()) is True


@pytest.mark.parametrize("status", ["fail", "error"])
def test_a_failed_or_errored_gate_fails_the_task(
    client: TestClient, db_session: Any, wired: list[dict[str, Any]], status: str
) -> None:
    _bound_suite(client, db_session)
    snippet.dataq_gate(**_context())
    _finish(db_session, status)

    with pytest.raises(Exception, match=r"DataQ gate (failed|error)") as exc:
        snippet.dataq_gate(**_context())
    assert type(exc.value).__name__ in ("DataQGateFailedError", "AirflowFailException")


def test_status_only_mode_never_triggers(
    client: TestClient,
    db_session: Any,
    wired: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bound_suite(client, db_session)
    monkeypatch.setenv("DATAQ_GATE_TRIGGER", "false")

    assert snippet.dataq_gate(**_context()) is False
    assert wired[0]["trigger"] is False
    assert db_session.scalars(select(Run)).first() is None
