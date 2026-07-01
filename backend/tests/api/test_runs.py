"""Run trigger + read API tests against a real Postgres (db_session).

get_db is overridden to the shared rolled-back session; auth runs in dev-bypass
(conftest), which upserts the dev user used as `created_by` for API-created
suites. `run_dispatch.dispatch_run` is stubbed by the autouse conftest fixture
(`stub_run_dispatch`), so triggering never touches a broker; a test that needs
the broker-failure path re-patches it. Skips without TEST_DATABASE_URL.
"""

import json
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.core.auth import get_current_user
from backend.app.db.models import (
    Check,
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
    # Canonical terminal-failed shape: finished_at set, started_at NULL (never
    # started) — matching the pipeline-trigger dispatch-failure path.
    assert run is not None and run.status == "failed"
    assert run.finished_at is not None
    assert run.started_at is None


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


def test_list_runs_workspace_admin_sees_all(
    client: TestClient, db_session: Any, make_workspace_admin: Callable[..., None]
) -> None:
    # A workspace-admin's run list spans every suite (ADR 0027), including runs of
    # a suite they don't own/share — unlike the owned-or-shared scoping above.
    dev = _user(db_session, "dev@ex")
    other = _user(db_session, "other@ex")
    theirs = _suite(db_session, other, target={"table": "T"})
    r = _run(db_session, theirs, status="succeeded")
    db_session.commit()

    make_workspace_admin(dev.email)
    _as(dev)
    body = client.get("/api/v1/runs").json()
    assert str(r.id) in {row["id"] for row in body}


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


def test_list_runs_includes_check_outcome_counts(client: TestClient, db_session: Any) -> None:
    # #423: the runs list surfaces each run's DQ outcome (total/passed/worst-severity)
    # — distinct from the run's execution `status`, which is `succeeded` even when
    # checks fail. total/passed count *evaluated* checks; operational skip/error are
    # excluded (matches the run-detail X/Y). A run with no results reports 0/0/None.
    dev = _user(db_session, "dev@ex")
    suite = _suite(db_session, dev, target={"table": "T"})
    failing = _run(db_session, suite, status="succeeded")  # executed, but a check failed
    clean = _run(db_session, suite, status="succeeded")  # no results yet
    operational = _run(db_session, suite, status="succeeded")  # only skip/error results
    # failing: pass/warn/fail + a skip (the skip must NOT count toward total).
    for name, st in [("c1", "pass"), ("c2", "warn"), ("c3", "fail"), ("c4", "skip")]:
        check = Check(suite_id=suite.id, name=name, expectation_type="x", config={})
        db_session.add(check)
        db_session.flush()
        db_session.add(Result(run_id=failing.id, check_id=check.id, status=st))
        db_session.add(Result(run_id=operational.id, check_id=check.id, status="skip"))
    db_session.commit()

    _as(dev)
    body = client.get("/api/v1/runs").json()
    rows = {r["id"]: r for r in body}

    bad = rows[str(failing.id)]
    assert bad["status"] == "succeeded"  # execution status unchanged
    assert (bad["checks_total"], bad["checks_passed"]) == (3, 1)  # skip excluded from total
    assert bad["worst_severity"] == "fail"  # worst of pass/warn/fail

    empty = rows[str(clean.id)]
    assert (empty["checks_total"], empty["checks_passed"], empty["worst_severity"]) == (0, 0, None)

    # An all-skip run has evaluated 0 checks → total 0 (renders "—", not green "0/N").
    op = rows[str(operational.id)]
    assert (op["checks_total"], op["checks_passed"], op["worst_severity"]) == (0, 0, None)


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
    # sample_failures is now exposed, but redacted at the boundary (#226). An
    # empty container redacts to itself (no values to mask).
    assert res["sample_failures"] == {"rows": []}


def test_get_run_redacts_sample_failure_values(client: TestClient, db_session: Any) -> None:
    """Raw failing cell values must be masked before leaving DataQ; the numeric
    counts and the row/column shape are kept (#226)."""

    dev = _user(db_session, "dev@ex")
    suite = _suite(db_session, dev, target={"table": "T"})
    check = Check(suite_id=suite.id, name="c", expectation_type="expect_x", config={})
    db_session.add(check)
    db_session.flush()
    run = _run(db_session, suite, status="succeeded")
    # A realistic GX sample: aggregate counts (safe) + the offending rows (PII).
    db_session.add(
        Result(
            run_id=run.id,
            check_id=check.id,
            status="fail",
            metric_value=Decimal("40.0"),
            sample_failures={
                "unexpected_count": 2,
                "unexpected_percent": 40.0,
                "partial_unexpected_list": [
                    {"id": 7, "email": "alice@example.com"},
                    {"id": 9, "email": "bob@example.com"},
                ],
            },
        )
    )
    db_session.commit()

    _as(dev)
    body = client.get(f"/api/v1/runs/{run.id}").json()
    sample = body["results"][0]["sample_failures"]

    # Counts kept; row count kept; the `id` locator surfaced (#415 column-aware), the
    # PII `email` masked.
    assert sample["unexpected_count"] == 2
    assert sample["unexpected_percent"] == 40.0
    assert len(sample["partial_unexpected_list"]) == 2
    assert sample["partial_unexpected_list"][0] == {"id": 7, "email": "<redacted>"}
    # The raw PII values must not appear anywhere in the serialized response.
    serialized = json.dumps(body)
    assert "alice@example.com" not in serialized
    assert "bob@example.com" not in serialized


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


# ───────────────────────── GET /runs/{id}/progress ─────────────────


def _check(db_session: Any, suite: Suite, name: str) -> Any:

    check = Check(suite_id=suite.id, name=name, expectation_type="expect_x", config={})
    db_session.add(check)
    db_session.flush()
    return check


def test_progress_running_run_all_checks_pending(client: TestClient, db_session: Any) -> None:
    """A running run with no results yet: every check pending, 0/N, zeroed counts."""
    dev = _user(db_session, "dev@ex")
    suite = _suite(db_session, dev, target={"table": "T"})
    _check(db_session, suite, "a")
    _check(db_session, suite, "b")
    db_session.commit()
    run = _run(db_session, suite, status="running")

    _as(dev)
    resp = client.get(f"/api/v1/runs/{run.id}/progress")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["total_checks"] == 2
    assert body["completed_checks"] == 0
    assert {c["name"]: c["status"] for c in body["checks"]} == {"a": None, "b": None}
    assert body["counts"]["pass"] == 0 and body["counts"]["error"] == 0


def test_progress_completed_run_reports_per_check_status_and_histogram(
    client: TestClient, db_session: Any
) -> None:
    """A finished run resolves each check to its result status; the histogram and
    completed count reflect the persisted rows (incl. operational `error`)."""
    dev = _user(db_session, "dev@ex")
    suite = _suite(db_session, dev, target={"table": "T"})
    c_pass = _check(db_session, suite, "ok")
    c_fail = _check(db_session, suite, "bad")
    c_err = _check(db_session, suite, "broken")
    _check(db_session, suite, "added_later")  # no result row → stays pending
    db_session.commit()
    run = _run(db_session, suite, status="succeeded")
    db_session.add_all(
        [
            Result(run_id=run.id, check_id=c_pass.id, status="pass"),
            Result(run_id=run.id, check_id=c_fail.id, status="fail", metric_value=Decimal("9")),
            Result(run_id=run.id, check_id=c_err.id, status="error"),
        ]
    )
    db_session.commit()

    _as(dev)
    body = client.get(f"/api/v1/runs/{run.id}/progress").json()
    assert body["status"] == "succeeded"
    assert body["total_checks"] == 4
    assert body["completed_checks"] == 3  # c_pending has no result row → pending
    by_name = {c["name"]: c["status"] for c in body["checks"]}
    assert by_name == {"ok": "pass", "bad": "fail", "broken": "error", "added_later": None}
    assert body["counts"]["pass"] == 1
    assert body["counts"]["fail"] == 1
    assert body["counts"]["error"] == 1
    assert body["counts"]["warn"] == 0


def test_progress_failed_run_has_terminal_status_and_no_results(
    client: TestClient, db_session: Any
) -> None:
    """A failed run rolls back and writes no results, so per-check status stays
    null — consumers must read it together with the terminal `status='failed'`,
    not treat null as 'still running' (the documented contract)."""
    dev = _user(db_session, "dev@ex")
    suite = _suite(db_session, dev, target={"table": "T"})
    _check(db_session, suite, "a")
    db_session.commit()
    run = _run(db_session, suite, status="failed")

    _as(dev)
    body = client.get(f"/api/v1/runs/{run.id}/progress").json()
    assert body["status"] == "failed"
    assert body["total_checks"] == 1
    assert body["completed_checks"] == 0
    assert body["checks"][0]["status"] is None


def test_progress_unknown_run_returns_404(client: TestClient, db_session: Any) -> None:
    dev = _user(db_session, "dev@ex")
    _as(dev)
    assert client.get(f"/api/v1/runs/{uuid.uuid4()}/progress").status_code == 404


def test_progress_no_access_returns_404(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "owner@ex")
    stranger = _user(db_session, "stranger@ex")
    suite = _suite(db_session, owner, target={"table": "T"})
    run = _run(db_session, suite, status="running")

    _as(stranger)
    assert client.get(f"/api/v1/runs/{run.id}/progress").status_code == 404


# ───────────────────────── POST /runs/{id}/cancel ──────────────────


@pytest.mark.parametrize("start_status", ["queued", "running"])
def test_cancel_non_terminal_run_marks_cancelled_and_revokes(
    client: TestClient, db_session: Any, monkeypatch: pytest.MonkeyPatch, start_status: str
) -> None:
    """A queued or running run cancels: status→cancelled, finished_at set, and the
    Celery task is revoked (best-effort) with the run's captured task id."""
    revoked: list[str | None] = []
    monkeypatch.setattr(run_dispatch, "revoke_run", lambda task_id: revoked.append(task_id))

    dev = _user(db_session, "dev@ex")
    suite = _suite(db_session, dev, target={"table": "T"})
    run = _run(db_session, suite, status=start_status)
    run.celery_task_id = "task-xyz"
    db_session.commit()

    _as(dev)
    resp = client.post(f"/api/v1/runs/{run.id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    db_session.refresh(run)
    assert run.status == "cancelled"
    assert run.finished_at is not None
    assert revoked == ["task-xyz"]


def test_cancel_terminal_run_returns_409(client: TestClient, db_session: Any) -> None:
    dev = _user(db_session, "dev@ex")
    suite = _suite(db_session, dev, target={"table": "T"})
    run = _run(db_session, suite, status="succeeded")

    _as(dev)
    resp = client.post(f"/api/v1/runs/{run.id}/cancel")
    assert resp.status_code == 409
    db_session.refresh(run)
    assert run.status == "succeeded"  # unchanged


def test_cancel_unknown_run_returns_404(client: TestClient, db_session: Any) -> None:
    dev = _user(db_session, "dev@ex")
    _as(dev)
    assert client.post(f"/api/v1/runs/{uuid.uuid4()}/cancel").status_code == 404


def test_cancel_no_access_returns_404(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "owner@ex")
    stranger = _user(db_session, "stranger@ex")
    suite = _suite(db_session, owner, target={"table": "T"})
    run = _run(db_session, suite, status="queued")

    _as(stranger)
    assert client.post(f"/api/v1/runs/{run.id}/cancel").status_code == 404


def test_cancel_requires_edit_permission(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "owner@ex")
    viewer = _user(db_session, "viewer@ex")
    suite = _suite(db_session, owner, target={"table": "T"})
    db_session.add(Share(suite_id=suite.id, user_id=viewer.id, permission="view"))
    run = _run(db_session, suite, status="queued")
    db_session.commit()

    _as(viewer)
    resp = client.post(f"/api/v1/runs/{run.id}/cancel")
    assert resp.status_code == 403
    db_session.refresh(run)
    assert run.status == "queued"  # unchanged


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


# ─────────────────────── GET /orchestration/pipelines ───────────────


def _pipeline_run_at(
    db_session: Any,
    conn: Connection,
    *,
    provider: str,
    pipeline: str,
    env: str,
    status: str,
    started_at: datetime | None,
) -> PipelineRun:
    pr = PipelineRun(
        provider=provider,
        connection_id=conn.id,
        provider_run_id=uuid.uuid4().hex,
        pipeline_or_dag_id=pipeline,
        env=env,
        status=status,
        started_at=started_at,
    )
    db_session.add(pr)
    db_session.commit()
    return pr


def test_list_pipelines_collapses_to_latest_run_per_pipeline(
    client: TestClient, db_session: Any
) -> None:
    """Two runs of the same pipeline → one row carrying the most-recent run."""
    owner = _user(db_session, "owner@ex")
    conn = _connection(db_session, owner, type_="adf")
    base = datetime(2026, 6, 1, tzinfo=UTC)
    _pipeline_run_at(
        db_session,
        conn,
        provider="adf",
        pipeline="etl",
        env="dev",
        status="failed",
        started_at=base,
    )
    _pipeline_run_at(
        db_session,
        conn,
        provider="adf",
        pipeline="etl",
        env="dev",
        status="succeeded",
        started_at=base + timedelta(hours=1),
    )

    _as(owner)
    rows = client.get("/api/v1/orchestration/pipelines").json()

    assert len(rows) == 1  # collapsed to the pipeline, not both runs
    assert rows[0]["pipeline_or_dag_id"] == "etl"
    assert rows[0]["status"] == "succeeded"  # the later run, not the earlier failure


def test_list_pipelines_one_row_per_pipeline_newest_active_first(
    client: TestClient, db_session: Any
) -> None:
    """Distinct (provider, pipeline, env) tuples each get a row; the most
    recently-active pipeline leads."""
    owner = _user(db_session, "owner@ex")
    adf = _connection(db_session, owner, type_="adf")
    af = _connection(db_session, owner, type_="airflow")
    base = datetime(2026, 6, 1, tzinfo=UTC)
    # same pipeline name in two envs is two distinct pipelines
    _pipeline_run_at(
        db_session,
        adf,
        provider="adf",
        pipeline="etl",
        env="dev",
        status="succeeded",
        started_at=base,
    )
    _pipeline_run_at(
        db_session,
        adf,
        provider="adf",
        pipeline="etl",
        env="qa",
        status="succeeded",
        started_at=base + timedelta(hours=2),
    )
    _pipeline_run_at(
        db_session,
        af,
        provider="airflow",
        pipeline="dag",
        env="dev",
        status="failed",
        started_at=base + timedelta(hours=1),
    )

    _as(owner)
    rows = client.get("/api/v1/orchestration/pipelines").json()

    keys = [(r["provider"], r["pipeline_or_dag_id"], r["env"]) for r in rows]
    assert keys == [
        ("adf", "etl", "qa"),  # base+2h — most recent
        ("airflow", "dag", "dev"),  # base+1h
        ("adf", "etl", "dev"),  # base
    ]


def test_list_pipelines_filters_by_provider_and_env(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "owner@ex")
    adf = _connection(db_session, owner, type_="adf")
    af = _connection(db_session, owner, type_="airflow")
    base = datetime(2026, 6, 1, tzinfo=UTC)
    _pipeline_run_at(
        db_session,
        adf,
        provider="adf",
        pipeline="etl",
        env="dev",
        status="succeeded",
        started_at=base,
    )
    _pipeline_run_at(
        db_session,
        adf,
        provider="adf",
        pipeline="etl",
        env="qa",
        status="succeeded",
        started_at=base,
    )
    _pipeline_run_at(
        db_session,
        af,
        provider="airflow",
        pipeline="dag",
        env="dev",
        status="failed",
        started_at=base,
    )

    _as(owner)
    adf_only = client.get("/api/v1/orchestration/pipelines?provider=adf").json()
    assert {r["provider"] for r in adf_only} == {"adf"}
    assert len(adf_only) == 2  # dev + qa

    dev_only = client.get("/api/v1/orchestration/pipelines?env=dev").json()
    assert {r["env"] for r in dev_only} == {"dev"}
    assert {(r["provider"], r["pipeline_or_dag_id"]) for r in dev_only} == {
        ("adf", "etl"),
        ("airflow", "dag"),
    }


def test_list_pipelines_newest_run_without_started_at_is_not_masked(
    client: TestClient, db_session: Any
) -> None:
    """Regression: a fresh run whose event carried no start time (started_at
    NULL — realistic for a failure webhook) must still win its partition. Naive
    `started_at DESC NULLS LAST` would rank it last and surface the stale older
    run instead; recency falls back to created_at."""
    owner = _user(db_session, "owner@ex")
    conn = _connection(db_session, owner, type_="adf")
    # older run, fully timed, succeeded — inserted first (earlier created_at)
    _pipeline_run_at(
        db_session,
        conn,
        provider="adf",
        pipeline="etl",
        env="dev",
        status="succeeded",
        started_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    # newest run, no start time, failed — inserted second (later created_at)
    _pipeline_run_at(
        db_session,
        conn,
        provider="adf",
        pipeline="etl",
        env="dev",
        status="failed",
        started_at=None,
    )

    _as(owner)
    rows = client.get("/api/v1/orchestration/pipelines").json()

    assert len(rows) == 1
    assert rows[0]["status"] == "failed"  # the freshest run, despite NULL started_at
    assert rows[0]["started_at"] is None


def test_list_pipelines_respects_limit(client: TestClient, db_session: Any) -> None:
    """`limit` caps to the N most-recently-active pipelines (parity with
    /pipeline_runs)."""
    owner = _user(db_session, "owner@ex")
    conn = _connection(db_session, owner, type_="adf")
    base = datetime(2026, 6, 1, tzinfo=UTC)
    for i in range(3):
        _pipeline_run_at(
            db_session,
            conn,
            provider="adf",
            pipeline=f"etl{i}",
            env="dev",
            status="succeeded",
            started_at=base + timedelta(hours=i),
        )

    _as(owner)
    rows = client.get("/api/v1/orchestration/pipelines?limit=2").json()

    assert len(rows) == 2
    # the two most-recently-active pipelines (etl2 @ +2h, etl1 @ +1h)
    assert [r["pipeline_or_dag_id"] for r in rows] == ["etl2", "etl1"]


def test_list_pipelines_requires_auth(db_session: Any) -> None:
    from fastapi import HTTPException

    app.dependency_overrides[get_db] = lambda: db_session

    def _reject() -> None:
        raise HTTPException(status_code=401, detail="unauthorized")

    app.dependency_overrides[get_current_user] = _reject
    try:
        assert TestClient(app).get("/api/v1/orchestration/pipelines").status_code == 401
    finally:
        app.dependency_overrides.clear()
