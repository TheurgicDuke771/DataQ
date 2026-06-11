"""Run trigger + read API tests against a real Postgres (db_session).

get_db is overridden to the shared rolled-back session; auth runs in dev-bypass
(conftest), which upserts the dev user used as `created_by` for API-created
suites. `run_dispatch.dispatch_run` is stubbed by the autouse conftest fixture
(`stub_run_dispatch`), so triggering never touches a broker; a test that needs
the broker-failure path re-patches it. Skips without TEST_DATABASE_URL.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.core.auth import get_current_user
from backend.app.db.models import (
    Connection,
    PipelineRun,
    Result,
    Run,
    Share,
    Suite,
    User,
)
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import run_dispatch


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _as(user: User) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


def _user(db_session: Any, email: str) -> User:
    u = User(aad_object_id=uuid.uuid4().hex, email=email)
    db_session.add(u)
    db_session.flush()
    return u


def _connection(db_session: Any, owner: User, *, type_: str = "snowflake") -> Connection:
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type=type_,
        env="dev",
        config={"account": "ab12345.eu-west-1"},
        secret_ref="kv-x",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.commit()
    return conn


def _suite(
    db_session: Any,
    owner: User,
    *,
    target: dict[str, Any] | None = None,
    type_: str = "snowflake",
) -> Suite:
    conn = _connection(db_session, owner, type_=type_)
    suite = Suite(name="s", connection_id=conn.id, created_by=owner.id, target=target)
    db_session.add(suite)
    db_session.commit()
    return suite


def _run(db_session: Any, suite: Suite, *, status: str = "queued", triggered_by: str = "t") -> Run:
    run = Run(suite_id=suite.id, status=status, triggered_by=triggered_by)
    db_session.add(run)
    db_session.commit()
    return run


# ───────────────────────── POST /suites/{id}/run ───────────────────


def test_trigger_creates_queued_run_and_dispatches(
    client: TestClient, db_session: Any, stub_run_dispatch: list[str]
) -> None:
    dev = _user(db_session, "dev@ex")
    _as(dev)
    suite = _suite(db_session, dev, target={"table": "ORDERS"})

    resp = client.post(f"/api/v1/suites/{suite.id}/run")

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"
    assert body["suite_id"] == str(suite.id)
    assert body["triggered_by"] == f"manual:{dev.id}"
    run = db_session.get(Run, uuid.UUID(body["id"]))
    assert run is not None and run.status == "queued"
    assert stub_run_dispatch == [body["id"]]


def test_trigger_targetless_suite_returns_422_and_creates_no_run(
    client: TestClient, db_session: Any, stub_run_dispatch: list[str]
) -> None:
    dev = _user(db_session, "dev@ex")
    _as(dev)
    suite = _suite(db_session, dev, target=None)

    resp = client.post(f"/api/v1/suites/{suite.id}/run")

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "suite_target_invalid"
    assert db_session.scalars(select(Run).where(Run.suite_id == suite.id)).all() == []
    assert stub_run_dispatch == []


def test_trigger_requires_edit_permission(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "owner@ex")
    viewer = _user(db_session, "viewer@ex")
    suite = _suite(db_session, owner, target={"table": "ORDERS"})
    db_session.add(Share(suite_id=suite.id, user_id=viewer.id, permission="view"))
    db_session.commit()

    _as(viewer)
    resp = client.post(f"/api/v1/suites/{suite.id}/run")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "suite_forbidden"


def test_trigger_no_access_returns_404(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "owner@ex")
    stranger = _user(db_session, "stranger@ex")
    suite = _suite(db_session, owner, target={"table": "ORDERS"})

    _as(stranger)
    resp = client.post(f"/api/v1/suites/{suite.id}/run")
    assert resp.status_code == 404


def test_trigger_broker_failure_marks_run_failed_and_503(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    dev = _user(db_session, "dev@ex")
    _as(dev)
    suite = _suite(db_session, dev, target={"table": "ORDERS"})

    def _boom(_run_id: Any) -> None:
        raise RuntimeError("broker down")

    monkeypatch.setattr(run_dispatch, "dispatch_run", _boom)
    resp = client.post(f"/api/v1/suites/{suite.id}/run")

    assert resp.status_code == 503
    run = db_session.scalars(select(Run).where(Run.suite_id == suite.id)).first()
    assert run is not None and run.status == "failed"


# ───────────────────────── GET /runs ───────────────────────────────


def test_list_runs_scoped_to_accessible_suites_newest_first(
    client: TestClient, db_session: Any
) -> None:
    dev = _user(db_session, "dev@ex")
    other = _user(db_session, "other@ex")
    mine = _suite(db_session, dev, target={"table": "T"})
    theirs = _suite(db_session, other, target={"table": "T"})
    r1 = _run(db_session, mine, status="succeeded")
    r2 = _run(db_session, mine, status="failed")
    _run(db_session, theirs)  # not accessible to dev
    # Postgres now() is transaction-scoped, so server-default created_at ties
    # inside this single test transaction; set distinct values so the desc
    # ordering is deterministic (in production each run is its own transaction).
    r1.created_at = datetime(2026, 6, 1, tzinfo=UTC)
    r2.created_at = datetime(2026, 6, 2, tzinfo=UTC)
    db_session.commit()

    _as(dev)
    body = client.get("/api/v1/runs").json()

    ids = [r["id"] for r in body]
    assert str(theirs.id) not in {r["suite_id"] for r in body}
    assert ids[:2] == [str(r2.id), str(r1.id)]  # newest (r2) first


def test_list_runs_filters_by_suite_and_status(client: TestClient, db_session: Any) -> None:
    dev = _user(db_session, "dev@ex")
    a = _suite(db_session, dev, target={"table": "T"})
    b = _suite(db_session, dev, target={"table": "T"})
    _run(db_session, a, status="succeeded")
    _run(db_session, a, status="failed")
    _run(db_session, b, status="succeeded")

    _as(dev)
    by_suite = client.get(f"/api/v1/runs?suite_id={a.id}").json()
    assert {r["suite_id"] for r in by_suite} == {str(a.id)}
    by_status = client.get(f"/api/v1/runs?suite_id={a.id}&status=failed").json()
    assert [r["status"] for r in by_status] == ["failed"]


def test_list_runs_inaccessible_suite_filter_returns_404(
    client: TestClient, db_session: Any
) -> None:
    dev = _user(db_session, "dev@ex")
    other = _user(db_session, "other@ex")
    theirs = _suite(db_session, other, target={"table": "T"})

    _as(dev)
    resp = client.get(f"/api/v1/runs?suite_id={theirs.id}")
    assert resp.status_code == 404


def test_list_runs_respects_limit(client: TestClient, db_session: Any) -> None:
    dev = _user(db_session, "dev@ex")
    s = _suite(db_session, dev, target={"table": "T"})
    for _ in range(3):
        _run(db_session, s)

    _as(dev)
    body = client.get("/api/v1/runs?limit=2").json()
    assert len(body) == 2
    assert client.get("/api/v1/runs?limit=0").status_code == 422  # below ge=1


# ───────────────────────── GET /runs/{id} ──────────────────────────


def test_get_run_returns_results(client: TestClient, db_session: Any) -> None:
    dev = _user(db_session, "dev@ex")
    suite = _suite(db_session, dev, target={"table": "T"})
    # a check for the result FK
    from backend.app.db.models import Check

    check = Check(suite_id=suite.id, name="c", expectation_type="expect_x", config={})
    db_session.add(check)
    db_session.flush()
    run = _run(db_session, suite, status="succeeded")
    db_session.add(
        Result(
            run_id=run.id,
            check_id=check.id,
            status="warn",
            metric_value=Decimal("2.5"),
            observed_value={"observed_value": 5},
            expected_value={"min_value": 1},
            sample_failures={"rows": []},
        )
    )
    db_session.commit()

    _as(dev)
    resp = client.get(f"/api/v1/runs/{run.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "succeeded"
    assert len(body["results"]) == 1
    res = body["results"][0]
    assert res["status"] == "warn"
    assert res["metric_value"] == 2.5
    assert res["observed_value"] == {"observed_value": 5}


def test_get_run_unknown_returns_404(client: TestClient, db_session: Any) -> None:
    dev = _user(db_session, "dev@ex")
    _as(dev)
    assert client.get(f"/api/v1/runs/{uuid.uuid4()}").status_code == 404


def test_get_run_no_access_returns_404(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "owner@ex")
    stranger = _user(db_session, "stranger@ex")
    suite = _suite(db_session, owner, target={"table": "T"})
    run = _run(db_session, suite)

    _as(stranger)
    assert client.get(f"/api/v1/runs/{run.id}").status_code == 404


# ───────────────────────── GET /pipeline_runs ──────────────────────


def _pipeline_run(db_session: Any, owner: User, *, provider: str, status: str) -> PipelineRun:
    conn = _connection(db_session, owner, type_=provider)
    pr = PipelineRun(
        provider=provider,
        connection_id=conn.id,
        provider_run_id=uuid.uuid4().hex,
        pipeline_or_dag_id="pipe",
        env="dev",
        status=status,
        started_at=datetime.now(UTC),
    )
    db_session.add(pr)
    db_session.commit()
    return pr


def test_list_pipeline_runs_filters_by_provider_and_status(
    client: TestClient, db_session: Any
) -> None:
    owner = _user(db_session, "owner@ex")
    _pipeline_run(db_session, owner, provider="adf", status="succeeded")
    _pipeline_run(db_session, owner, provider="airflow", status="failed")

    _as(owner)
    adf = client.get("/api/v1/pipeline_runs?provider=adf").json()
    assert {p["provider"] for p in adf} == {"adf"}
    failed = client.get("/api/v1/pipeline_runs?status=failed").json()
    assert {p["status"] for p in failed} == {"failed"}
    assert {p["provider"] for p in failed} == {"airflow"}


def test_list_pipeline_runs_requires_auth(db_session: Any) -> None:
    from fastapi import HTTPException

    app.dependency_overrides[get_db] = lambda: db_session

    def _reject() -> None:
        raise HTTPException(status_code=401, detail="unauthorized")

    app.dependency_overrides[get_current_user] = _reject
    try:
        assert TestClient(app).get("/api/v1/pipeline_runs").status_code == 401
    finally:
        app.dependency_overrides.clear()
