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
from backend.app.core.config import get_settings
from backend.app.db.models import (
    Asset,
    Check,
    Connection,
    PipelineRun,
    Result,
    Run,
    Share,
    Suite,
    TriggerBinding,
    User,
    WorkspaceHealth,
)
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services import run_dispatch, workspace_health_service


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


def test_trigger_stamps_suite_asset_id_on_run(
    client: TestClient, db_session: Any, stub_run_dispatch: list[str]
) -> None:
    """A manually-triggered run records the suite's resolved asset (ADR 0034):
    run.asset_id == suite.asset_id at dispatch."""
    dev = _user(db_session, "dev@ex")
    _as(dev)
    suite = _suite(db_session, dev, target={"table": "ORDERS"})
    # Attach a resolved asset to the suite (as the save hook would have).
    asset = Asset(namespace="snowflake://acct", name="DB.SCHEMA.ORDERS", env="dev")
    db_session.add(asset)
    db_session.flush()
    suite.asset_id = asset.id
    db_session.commit()

    resp = client.post(f"/api/v1/suites/{suite.id}/run")

    assert resp.status_code == 202
    run = db_session.get(Run, uuid.UUID(resp.json()["id"]))
    assert run.asset_id == asset.id


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
    # A user-visible dispatch-failure reason is recorded + surfaced (#605).
    assert run.failure_reason == run_dispatch.DISPATCH_FAILED_REASON
    detail = client.get(f"/api/v1/runs/{run.id}").json()
    assert detail["failure_reason"] == run_dispatch.DISPATCH_FAILED_REASON


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
    resp = client.get("/api/v1/runs?limit=0")
    assert resp.status_code == 422  # below ge=1


def test_list_runs_total_count_header_matches_accessible_population(
    client: TestClient, db_session: Any
) -> None:
    """#1108: `/runs` previously had `limit` only — no `offset`, no total, so a
    page shorter than `limit` could never be told apart from "that's everything".
    `X-Total-Count` reports the caller's ACCESSIBLE population (suite-scoped,
    unlike the workspace-true `/assets` total), unaffected by the page size."""
    dev = _user(db_session, "dev@ex")
    other = _user(db_session, "other@ex")
    mine = _suite(db_session, dev, target={"table": "T"})
    theirs = _suite(db_session, other, target={"table": "T"})
    for _ in range(5):
        _run(db_session, mine)
    _run(db_session, theirs)  # not accessible to dev — must not inflate the total

    _as(dev)
    resp = client.get("/api/v1/runs?limit=2")
    assert resp.status_code == 200
    assert resp.headers["x-total-count"] == "5"
    assert len(resp.json()) == 2  # the page is still truncated to `limit`


def test_list_runs_total_count_header_respects_filters(client: TestClient, db_session: Any) -> None:
    """The header counts the SAME filtered population the list applies (#1108) —
    a `status` filter narrows both, not just the page."""
    dev = _user(db_session, "dev@ex")
    s = _suite(db_session, dev, target={"table": "T"})
    _run(db_session, s, status="succeeded")
    _run(db_session, s, status="succeeded")
    _run(db_session, s, status="failed")

    _as(dev)
    resp = client.get(f"/api/v1/runs?suite_id={s.id}&status=succeeded")
    assert resp.headers["x-total-count"] == "2"
    assert len(resp.json()) == 2


def test_list_runs_pages_with_offset_no_duplicates(client: TestClient, db_session: Any) -> None:
    """`offset` actually pages `/runs` now (#1108), mirroring the `/pipeline_runs`
    (#928) and `/incidents` (#772) paging shape."""
    dev = _user(db_session, "dev@ex")
    s = _suite(db_session, dev, target={"table": "T"})
    for _ in range(5):
        _run(db_session, s)
    _as(dev)

    page1 = client.get("/api/v1/runs?limit=2&offset=0").json()
    page2 = client.get("/api/v1/runs?limit=2&offset=2").json()
    page3 = client.get("/api/v1/runs?limit=2&offset=4").json()
    assert [len(page1), len(page2), len(page3)] == [2, 2, 1]
    ids = [r["id"] for r in page1 + page2 + page3]
    assert len(set(ids)) == 5, "paging returned a duplicate row"


def test_list_runs_rejects_an_unknown_status(client: TestClient, db_session: Any) -> None:
    """A typo'd/wrong-case `status` must 422, not answer a confident empty page.

    `/runs` validated nothing, so `?status=succeded` returned `200 []` with
    `X-Total-Count: 0` — a total that asserts "no runs are in that status" about a
    status that does not exist. That is the confidently-empty-answer class (#828),
    already guarded on `/pipeline_runs`'s `provider` (#306) and `/incidents`'s
    `state` (#570). Seed a real run first so an empty body could ONLY come from the
    filter, never from an empty table.
    """
    dev = _user(db_session, "dev@ex")
    s = _suite(db_session, dev, target={"table": "T"})
    _run(db_session, s, status="succeeded")
    _as(dev)

    resp = client.get("/api/v1/runs?status=succeded")  # typo
    assert resp.status_code == 422
    assert "succeeded" in resp.json()["error"]["detail"]["allowed"]

    # Right name, wrong case — the column stores lower-case, so this matched
    # nothing and was the same silent lie.
    resp = client.get("/api/v1/runs?status=Succeeded")
    assert resp.status_code == 422

    # The valid value still works and still returns the seeded row…
    resp = client.get("/api/v1/runs?status=succeeded")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.headers["x-total-count"] == "1"
    # …and omitting the filter is not "unknown" — it means no filter.
    resp = client.get("/api/v1/runs")
    assert resp.status_code == 200


def test_list_runs_status_validated_before_the_total_header_is_computed(
    client: TestClient, db_session: Any
) -> None:
    """The 422 must carry NO `X-Total-Count` at all.

    A `0` alongside the error would still be an assertion about a population that
    was never queried — the header has to be absent, not zero, when the filter is
    rejected."""
    dev = _user(db_session, "dev@ex")
    _run(db_session, _suite(db_session, dev, target={"table": "T"}), status="succeeded")
    _as(dev)

    resp = client.get("/api/v1/runs?status=nope")
    assert resp.status_code == 422
    assert "x-total-count" not in resp.headers


def test_list_runs_status_gate_runs_for_a_named_suite_too(
    client: TestClient, db_session: Any
) -> None:
    """`?suite_id=…&status=<bogus>` 422s as well — the suite gate runs first, so
    the status check must not be skipped on the branch that passes it."""
    dev = _user(db_session, "dev@ex")
    s = _suite(db_session, dev, target={"table": "T"})
    _run(db_session, s, status="succeeded")
    _as(dev)

    resp = client.get(f"/api/v1/runs?suite_id={s.id}&status=succeded")
    assert resp.status_code == 422


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


def test_get_run_detail_grafts_check_outcome_counts(client: TestClient, db_session: Any) -> None:
    """The detail endpoint must graft the data-quality outcome like the list does
    — a bare RunRead.model_validate(run) leaves checks_total/passed at 0/0 because
    the ORM Run has no such columns (#571)."""
    dev = _user(db_session, "dev@ex")
    suite = _suite(db_session, dev, target={"table": "T"})
    c1 = _check(db_session, suite, "pass-check")
    c2 = _check(db_session, suite, "warn-check")
    run = _run(db_session, suite, status="succeeded")
    db_session.add_all(
        [
            Result(run_id=run.id, check_id=c1.id, status="pass"),
            Result(run_id=run.id, check_id=c2.id, status="warn"),
        ]
    )
    db_session.commit()

    _as(dev)
    body = client.get(f"/api/v1/runs/{run.id}").json()
    assert (body["checks_total"], body["checks_passed"], body["worst_severity"]) == (2, 1, "warn")

    # All-skip run (present Result rows, but every check operational): evaluated
    # total is 0 via a present-but-zeroed tuple — the detail graft must render `—`
    # (0/0/None), the truthy-tuple path distinct from the no-rows None path above.
    skip_run = _run(db_session, suite, status="succeeded")
    db_session.add(Result(run_id=skip_run.id, check_id=c1.id, status="skip"))
    db_session.commit()
    skip_body = client.get(f"/api/v1/runs/{skip_run.id}").json()
    assert (
        skip_body["checks_total"],
        skip_body["checks_passed"],
        skip_body["worst_severity"],
    ) == (0, 0, None)


def test_pre_dispatch_failure_checks_total_consistent_across_read_models(
    client: TestClient, db_session: Any
) -> None:
    """A run that fails before any check executes has no Result rows: both the list
    and the detail report checks_total == 0 (the evaluated-outcome denominator,
    rendered `—`), while /progress reports the suite's *defined* check count. The
    two are truthful about different things; list and detail must agree (#571)."""
    dev = _user(db_session, "dev@ex")
    suite = _suite(db_session, dev, target={"table": "T"})
    _check(db_session, suite, "a")
    _check(db_session, suite, "b")
    run = _run(db_session, suite, status="failed")  # failed before dispatch — no results
    db_session.commit()

    _as(dev)
    detail = client.get(f"/api/v1/runs/{run.id}").json()
    listed = next(r for r in client.get("/api/v1/runs").json() if r["id"] == str(run.id))
    progress = client.get(f"/api/v1/runs/{run.id}/progress").json()

    assert detail["checks_total"] == listed["checks_total"] == 0  # read models agree
    assert progress["total_checks"] == 2  # suite size — the intentional difference


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
    # #424: the wire payload also carries an honest, explicit redaction summary —
    # `id` shown + `email` masked is a partial mix.
    result = body["results"][0]
    assert result["redaction"] == "partial"
    assert result["redacted_columns"] == ["email"]


# ── #424: the read API's `redaction` state must match reality per result ──────


def test_get_run_reports_full_redaction_state_when_every_column_masked(
    client: TestClient, db_session: Any
) -> None:
    dev = _user(db_session, "dev@ex")
    suite = _suite(db_session, dev, target={"table": "T"})
    # A PII tested column: the whole scalar list masks, and there is nothing else
    # in the sample to surface.
    check = Check(
        suite_id=suite.id, name="c", expectation_type="expect_x", config={"column": "EMAIL"}
    )
    db_session.add(check)
    db_session.flush()
    run = _run(db_session, suite, status="succeeded")
    db_session.add(
        Result(
            run_id=run.id,
            check_id=check.id,
            status="fail",
            sample_failures={"partial_unexpected_list": ["alice@example.com", "bob@example.com"]},
        )
    )
    db_session.commit()

    _as(dev)
    result = client.get(f"/api/v1/runs/{run.id}").json()["results"][0]
    assert result["redaction"] == "full"
    assert result["redacted_columns"] == ["EMAIL"]
    assert result["sample_failures"] == {"partial_unexpected_list": ["<redacted>", "<redacted>"]}


def test_get_run_reports_none_redaction_state_when_every_column_shown(
    client: TestClient, db_session: Any
) -> None:
    dev = _user(db_session, "dev@ex")
    suite = _suite(db_session, dev, target={"table": "T"})
    # A non-PII tested column (#417): its failing values surface untouched.
    check = Check(
        suite_id=suite.id, name="c", expectation_type="expect_x", config={"column": "LINE_TOTAL"}
    )
    db_session.add(check)
    db_session.flush()
    run = _run(db_session, suite, status="succeeded")
    db_session.add(
        Result(
            run_id=run.id,
            check_id=check.id,
            status="fail",
            sample_failures={"partial_unexpected_list": [-12.5, -5.0]},
        )
    )
    db_session.commit()

    _as(dev)
    result = client.get(f"/api/v1/runs/{run.id}").json()["results"][0]
    assert result["redaction"] == "none"
    assert result["redacted_columns"] == []
    assert result["sample_failures"] == {"partial_unexpected_list": [-12.5, -5.0]}


def test_get_run_reports_null_redaction_state_when_sample_has_no_data_bearing_content(
    client: TestClient, db_session: Any
) -> None:
    """Only aggregate counts (no row/value data at all) — there is nothing to have
    redacted one way or the other, so the state must be null, not a guess."""
    dev = _user(db_session, "dev@ex")
    suite = _suite(db_session, dev, target={"table": "T"})
    check = Check(suite_id=suite.id, name="c", expectation_type="expect_x", config={})
    db_session.add(check)
    db_session.flush()
    run = _run(db_session, suite, status="succeeded")
    db_session.add(
        Result(
            run_id=run.id,
            check_id=check.id,
            status="fail",
            sample_failures={"unexpected_count": 3, "unexpected_percent": 12.5},
        )
    )
    db_session.commit()

    _as(dev)
    result = client.get(f"/api/v1/runs/{run.id}").json()["results"][0]
    assert result["redaction"] is None
    assert result["redacted_columns"] == []


def test_get_run_reports_null_redaction_state_when_no_sample(
    client: TestClient, db_session: Any
) -> None:
    dev = _user(db_session, "dev@ex")
    suite = _suite(db_session, dev, target={"table": "T"})
    check = Check(suite_id=suite.id, name="c", expectation_type="expect_x", config={})
    db_session.add(check)
    db_session.flush()
    run = _run(db_session, suite, status="succeeded")
    db_session.add(Result(run_id=run.id, check_id=check.id, status="pass"))
    db_session.commit()

    _as(dev)
    result = client.get(f"/api/v1/runs/{run.id}").json()["results"][0]
    assert result["sample_failures"] is None
    assert result["redaction"] is None
    assert result["redacted_columns"] == []


def test_get_run_unknown_returns_404(client: TestClient, db_session: Any) -> None:
    dev = _user(db_session, "dev@ex")
    _as(dev)
    resp = client.get(f"/api/v1/runs/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_get_run_no_access_returns_404(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "owner@ex")
    stranger = _user(db_session, "stranger@ex")
    suite = _suite(db_session, owner, target={"table": "T"})
    run = _run(db_session, suite)

    _as(stranger)
    resp = client.get(f"/api/v1/runs/{run.id}")
    assert resp.status_code == 404


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
    resp = client.get(f"/api/v1/runs/{uuid.uuid4()}/progress")
    assert resp.status_code == 404


def test_progress_no_access_returns_404(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "owner@ex")
    stranger = _user(db_session, "stranger@ex")
    suite = _suite(db_session, owner, target={"table": "T"})
    run = _run(db_session, suite, status="running")

    _as(stranger)
    resp = client.get(f"/api/v1/runs/{run.id}/progress")
    assert resp.status_code == 404


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
    resp = client.post(f"/api/v1/runs/{uuid.uuid4()}/cancel")
    assert resp.status_code == 404


def test_cancel_no_access_returns_404(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "owner@ex")
    stranger = _user(db_session, "stranger@ex")
    suite = _suite(db_session, owner, target={"table": "T"})
    run = _run(db_session, suite, status="queued")

    _as(stranger)
    resp = client.post(f"/api/v1/runs/{run.id}/cancel")
    assert resp.status_code == 404


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


def _pipeline_runs_on_one_connection(
    db_session: Any, owner: User, *, count: int, provider: str = "adf"
) -> list[PipelineRun]:
    """`count` pipeline runs sharing ONE connection.

    `_pipeline_run` above mints a connection per call, which trips
    `uq_connections_orchestrator_type_env` on the second row — an orchestrator is
    singular per (type, env). Real pipeline runs all hang off one connection
    anyway, so this is also the more faithful shape for paging tests.
    """
    conn = _connection(db_session, owner, type_=provider)
    runs = [
        PipelineRun(
            provider=provider,
            connection_id=conn.id,
            provider_run_id=uuid.uuid4().hex,
            pipeline_or_dag_id="pipe",
            env="dev",
            status="succeeded",
            started_at=datetime.now(UTC),
        )
        for _ in range(count)
    ]
    db_session.add_all(runs)
    db_session.commit()
    return runs


def test_list_pipeline_runs_pages_with_offset(client: TestClient, db_session: Any) -> None:
    """`offset` actually pages, and never returns the same row twice (#928).

    Before this, `offset` was not a parameter at all — FastAPI silently discarded
    it, so a paging client re-read page 1 forever while believing it was advancing.
    """
    owner = _user(db_session, "owner@ex")
    _pipeline_runs_on_one_connection(db_session, owner, count=5)
    _as(owner)

    page1 = client.get("/api/v1/pipeline_runs?limit=2&offset=0").json()
    page2 = client.get("/api/v1/pipeline_runs?limit=2&offset=2").json()
    page3 = client.get("/api/v1/pipeline_runs?limit=2&offset=4").json()

    assert [len(page1), len(page2), len(page3)] == [2, 2, 1]
    ids = [p["id"] for p in page1 + page2 + page3]
    assert len(set(ids)) == 5, "paging returned a duplicate row"
    # And the pages together are the whole set, in order — no row skipped.
    everything = [p["id"] for p in client.get("/api/v1/pipeline_runs?limit=200").json()]
    assert ids == everything


def test_pipeline_runs_total_count_header_matches_full_population(
    client: TestClient, db_session: Any
) -> None:
    """#1108: `/pipeline_runs` had `offset` (#928) but no total — a page shorter
    than `limit` couldn't be told apart from "that's everything". `X-Total-Count`
    reports the true population regardless of the page size."""
    owner = _user(db_session, "owner@ex")
    _pipeline_runs_on_one_connection(db_session, owner, count=5)
    _as(owner)

    resp = client.get("/api/v1/pipeline_runs?limit=2")
    assert resp.status_code == 200
    assert resp.headers["x-total-count"] == "5"
    assert len(resp.json()) == 2


def test_pipeline_runs_total_count_header_respects_filters(
    client: TestClient, db_session: Any
) -> None:
    """The header counts the SAME `provider`/`status`-filtered population the
    list applies (#1108) — not the unfiltered table."""
    owner = _user(db_session, "owner@ex")
    _pipeline_run(db_session, owner, provider="adf", status="succeeded")
    _pipeline_run(db_session, owner, provider="airflow", status="failed")
    _as(owner)

    resp = client.get("/api/v1/pipeline_runs?provider=adf")
    assert resp.headers["x-total-count"] == "1"
    resp = client.get("/api/v1/pipeline_runs?status=failed")
    assert resp.headers["x-total-count"] == "1"
    resp = client.get("/api/v1/pipeline_runs")
    assert resp.headers["x-total-count"] == "2"


def test_pipeline_run_ordering_is_total_so_paging_cannot_skip() -> None:
    """The paging order must be TOTAL — it ends in a unique column (#928).

    Why this is a structural assertion and not a behavioural one, stated because
    the behavioural version is tempting and worthless: the poll ingests a batch in
    ONE transaction and Postgres' `now()` is transaction-scoped, so real batches
    land with identical `created_at`. Ordering by that alone is not a total order,
    and `LIMIT/OFFSET` over a non-total order may return a row on two pages while
    never returning another.

    *May.* Postgres is free to vary the order of tied rows but is not obliged to,
    and in practice a small table with a stable plan returns them consistently. A
    test that pages tied rows and asserts no duplicates therefore **passes against
    the unfixed code** most of the time — verified: removing the `id` tie-break
    left that test green. That is the #948 coin-flip lesson exactly, so this
    asserts the invariant itself rather than waiting for the database to misbehave
    on cue.
    """
    from backend.app.services.orchestration_service import pipeline_run_order_by

    order = pipeline_run_order_by()
    assert len(order) >= 2, "a single non-unique sort key cannot be a total order"
    # Totality is exactly "the final sort key is unique", so assert that property
    # rather than the identity of one particular column — a different unique key
    # would be equally correct, and this stays true if the ordering is reworked.
    final_key = order[-1].element
    final_name = getattr(final_key, "name", final_key)
    assert getattr(
        final_key, "primary_key", False
    ), f"paging order must end in a unique column; ends in {final_name!r}"


def test_list_pipeline_runs_rejects_an_unknown_provider(
    client: TestClient, db_session: Any
) -> None:
    """A typo'd provider must 422, not return a confident empty list (#306).

    The pre-fix behaviour was `200 []` — indistinguishable from "this provider has
    no runs", which is the confidently-empty-answer class (#828). Seed a real run
    first so an empty body could ONLY come from the filter, never from an empty
    table.
    """
    owner = _user(db_session, "owner@ex")
    _pipeline_run(db_session, owner, provider="adf", status="succeeded")
    _as(owner)

    resp = client.get("/api/v1/pipeline_runs?provider=ADF")  # right name, wrong case
    assert resp.status_code == 422
    assert "adf" in resp.json()["error"]["detail"]["allowed"]

    resp = client.get("/api/v1/pipeline_runs?provider=nope")
    assert resp.status_code == 422
    # The valid value still works, and still returns the seeded row.
    resp = client.get("/api/v1/pipeline_runs?provider=adf")
    assert len(resp.json()) == 1
    # Omitting the filter is not "unknown" — it means no filter.
    resp = client.get("/api/v1/pipeline_runs")
    assert resp.status_code == 200


def test_list_pipeline_runs_rejects_an_unknown_status(client: TestClient, db_session: Any) -> None:
    """`status` joins `provider` behind the same closed-vocabulary gate (#1108).

    `provider` has 422'd since #306, but `status` flowed straight into the `WHERE`
    — so `?status=succeded` answered `200 []` with `X-Total-Count: 0`, the exact
    confidently-empty-answer shape (#828) the provider gate exists to prevent. The
    422 must also carry no total: a `0` would assert something about a population
    that was never queried."""
    owner = _user(db_session, "owner@ex")
    _pipeline_run(db_session, owner, provider="adf", status="succeeded")
    _as(owner)

    resp = client.get("/api/v1/pipeline_runs?status=succeded")  # typo
    assert resp.status_code == 422
    assert "succeeded" in resp.json()["error"]["detail"]["allowed"]
    assert "x-total-count" not in resp.headers

    resp = client.get("/api/v1/pipeline_runs?status=Succeeded")  # wrong case
    assert resp.status_code == 422

    # The valid value still works, and still returns the seeded row + its total.
    resp = client.get("/api/v1/pipeline_runs?status=succeeded")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.headers["x-total-count"] == "1"


def test_list_pipelines_rejects_unknown_provider_and_env(
    client: TestClient, db_session: Any
) -> None:
    """Same guard on `/orchestration/pipelines`, which takes `env` too (#306)."""
    owner = _user(db_session, "owner@ex")
    _pipeline_run(db_session, owner, provider="adf", status="succeeded")
    _as(owner)

    resp = client.get("/api/v1/orchestration/pipelines?provider=nope")
    assert resp.status_code == 422
    resp = client.get("/api/v1/orchestration/pipelines?env=production")  # real env is "prod"
    assert resp.status_code == 422
    assert "prod" in resp.json()["error"]["detail"]["allowed"]
    resp = client.get("/api/v1/orchestration/pipelines?provider=adf&env=dev")
    assert resp.status_code == 200
    resp = client.get("/api/v1/orchestration/pipelines")
    assert resp.status_code == 200


def test_list_pipeline_runs_requires_auth(db_session: Any) -> None:
    from fastapi import HTTPException

    app.dependency_overrides[get_db] = lambda: db_session

    def _reject() -> None:
        raise HTTPException(status_code=401, detail="unauthorized")

    app.dependency_overrides[get_current_user] = _reject
    try:
        resp = TestClient(app).get("/api/v1/pipeline_runs")
        assert resp.status_code == 401
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
        resp = TestClient(app).get("/api/v1/orchestration/pipelines")
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


# ─────────────────────── GET /orchestration/near-misses (#1199) ───────────────


def _binding(
    db_session: Any, suite: Suite, *, provider: str, pipeline: str, env: str, enabled: bool = True
) -> TriggerBinding:
    binding = TriggerBinding(
        provider=provider,
        pipeline_or_dag_id=pipeline,
        env=env,
        suite_id=suite.id,
        enabled=enabled,
    )
    db_session.add(binding)
    db_session.commit()
    return binding


def _record_near_miss(
    db_session: Any,
    *,
    provider: str,
    pipeline: str,
    run_env: str,
    binding_env: str,
    updated_at: datetime | None = None,
) -> None:
    workspace_health_service.record_trigger_binding_env_near_miss(
        db_session,
        provider=provider,
        pipeline_or_dag_id=pipeline,
        run_env=run_env,
        binding_env=binding_env,
    )
    if updated_at is not None:
        # Backdate the row directly, bypassing the write path's `func.now()`, so
        # the "aged out of the recency window" case can be exercised without a
        # real clock wait.
        key = workspace_health_service._near_miss_key(
            provider=provider,
            pipeline_or_dag_id=pipeline,
            run_env=run_env,
            binding_env=binding_env,
        )
        row = db_session.get(WorkspaceHealth, key)
        assert row is not None
        row.updated_at = updated_at
        db_session.commit()


def test_list_near_misses_decodes_a_current_row(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "owner@ex")
    suite = _suite(db_session, owner)
    _binding(db_session, suite, provider="airflow", pipeline="flow_a", env="dev")
    _record_near_miss(
        db_session, provider="airflow", pipeline="flow_a", run_env="qa", binding_env="dev"
    )

    _as(owner)
    rows = client.get("/api/v1/orchestration/near-misses").json()

    assert len(rows) == 1
    assert rows[0]["provider"] == "airflow"
    assert rows[0]["pipeline_or_dag_id"] == "flow_a"
    assert rows[0]["run_env"] == "qa"
    assert rows[0]["binding_env"] == "dev"


def test_list_near_misses_excludes_a_stale_row(client: TestClient, db_session: Any) -> None:
    """A near-miss whose `updated_at` has aged past the recency window reads as
    resolved (fixed, or the pipeline stopped running) rather than ongoing."""
    owner = _user(db_session, "owner@ex")
    suite = _suite(db_session, owner)
    _binding(db_session, suite, provider="airflow", pipeline="flow_a", env="dev")
    stale = datetime.now(UTC) - timedelta(
        hours=get_settings().trigger_env_near_miss_recent_hours + 1
    )
    _record_near_miss(
        db_session,
        provider="airflow",
        pipeline="flow_a",
        run_env="qa",
        binding_env="dev",
        updated_at=stale,
    )

    _as(owner)
    rows = client.get("/api/v1/orchestration/near-misses").json()

    assert rows == []


def test_list_near_misses_excludes_a_disabled_binding(client: TestClient, db_session: Any) -> None:
    """No ENABLED binding for the (provider, pipeline) → nothing to re-derive the
    candidate hash from, so a stray row (e.g. left over from before the binding
    was disabled) can never be matched back to a tuple here."""
    owner = _user(db_session, "owner@ex")
    suite = _suite(db_session, owner)
    _binding(db_session, suite, provider="airflow", pipeline="flow_a", env="dev", enabled=False)
    _record_near_miss(
        db_session, provider="airflow", pipeline="flow_a", run_env="qa", binding_env="dev"
    )

    _as(owner)
    rows = client.get("/api/v1/orchestration/near-misses").json()

    assert rows == []


def test_list_near_misses_no_bindings_returns_empty(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "owner@ex")
    _as(owner)
    rows = client.get("/api/v1/orchestration/near-misses").json()
    assert rows == []


def test_list_near_misses_omits_bindings_on_inaccessible_suites(
    client: TestClient, db_session: Any
) -> None:
    """The authz gate this endpoint hangs on (#1199 review). A trigger binding is
    suite-owned config — `GET /trigger-bindings` never shows a stranger someone
    else's binding, and neither may this route, or it becomes a workspace-wide
    enumeration of (provider, pipeline_or_dag_id, binding_env) tuples."""
    owner = _user(db_session, "owner@ex")
    stranger = _user(db_session, "stranger@ex")
    suite = _suite(db_session, owner)
    _binding(db_session, suite, provider="airflow", pipeline="flow_a", env="dev")
    _record_near_miss(
        db_session, provider="airflow", pipeline="flow_a", run_env="qa", binding_env="dev"
    )

    _as(owner)
    assert len(client.get("/api/v1/orchestration/near-misses").json()) == 1

    _as(stranger)
    assert client.get("/api/v1/orchestration/near-misses").json() == []


def test_list_near_misses_includes_shared_suites(client: TestClient, db_session: Any) -> None:
    owner = _user(db_session, "owner@ex")
    sharee = _user(db_session, "sharee@ex")
    suite = _suite(db_session, owner)
    db_session.add(Share(suite_id=suite.id, user_id=sharee.id, permission="view"))
    db_session.commit()
    _binding(db_session, suite, provider="airflow", pipeline="flow_a", env="dev")
    _record_near_miss(
        db_session, provider="airflow", pipeline="flow_a", run_env="qa", binding_env="dev"
    )

    _as(sharee)
    assert len(client.get("/api/v1/orchestration/near-misses").json()) == 1


def test_list_near_misses_suite_id_narrows_to_one_suite(
    client: TestClient, db_session: Any
) -> None:
    owner = _user(db_session, "owner@ex")
    suite_a = _suite(db_session, owner)
    suite_b = _suite(db_session, owner)
    _binding(db_session, suite_a, provider="airflow", pipeline="flow_a", env="dev")
    _binding(db_session, suite_b, provider="airflow", pipeline="flow_b", env="dev")
    for pipeline in ("flow_a", "flow_b"):
        _record_near_miss(
            db_session, provider="airflow", pipeline=pipeline, run_env="qa", binding_env="dev"
        )

    _as(owner)
    assert len(client.get("/api/v1/orchestration/near-misses").json()) == 2
    scoped = client.get(f"/api/v1/orchestration/near-misses?suite_id={suite_a.id}").json()
    assert [r["pipeline_or_dag_id"] for r in scoped] == ["flow_a"]


def test_list_near_misses_returns_every_current_mismatch_on_one_binding(
    client: TestClient, db_session: Any
) -> None:
    """The #1186 root case: one DAG id reported by two orchestrator connections in
    two different wrong envs. Both are live and both must surface — a UI that
    showed only the first would hide a real mismatch behind another."""
    owner = _user(db_session, "owner@ex")
    suite = _suite(db_session, owner)
    _binding(db_session, suite, provider="airflow", pipeline="flow_a", env="dev")
    for run_env in ("qa", "uat"):
        _record_near_miss(
            db_session, provider="airflow", pipeline="flow_a", run_env=run_env, binding_env="dev"
        )

    _as(owner)
    rows = client.get("/api/v1/orchestration/near-misses").json()

    assert sorted(r["run_env"] for r in rows) == ["qa", "uat"]


def test_list_near_misses_requires_auth(db_session: Any) -> None:
    from fastapi import HTTPException

    app.dependency_overrides[get_db] = lambda: db_session

    def _reject() -> None:
        raise HTTPException(status_code=401, detail="unauthorized")

    app.dependency_overrides[get_current_user] = _reject
    try:
        resp = TestClient(app).get("/api/v1/orchestration/near-misses")
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()
