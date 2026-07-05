"""DB-backed tests for the 8 MCP tools (real Postgres).

Each tool is a thin wrapper that opens a session, resolves the caller, and calls
the service layer with per-suite authz. We isolate the tool *logic* by patching
`server.get_session` → the test session and `server.resolve_current_user` → a
known user, then assert the returned LLM-shaped dict and that authz is enforced.
The auth/user-resolution itself is covered in test_mcp_auth.py. Skips without
TEST_DATABASE_URL.
"""

import uuid
from typing import Any

import pytest
from fastmcp.exceptions import ToolError

from backend.app.db.models import Check, Connection, PipelineRun, Result, Run, Suite, User
from backend.app.mcp import server
from backend.app.services import profile_service, run_dispatch


def _user(db_session: Any, email: str = "ada@acme.io") -> User:
    u = User(aad_object_id=uuid.uuid4().hex, email=email)
    db_session.add(u)
    db_session.flush()
    return u


def _suite(db_session: Any, owner: User, *, with_target: bool = True) -> Suite:
    conn = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "a", "schema": "PUBLIC"},
        secret_ref="kv-sf",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(
        name="Orders",
        connection_id=conn.id,
        created_by=owner.id,
        target={"table": "ORDERS"} if with_target else None,
    )
    db_session.add(suite)
    db_session.commit()
    return suite


def _as(monkeypatch: Any, db_session: Any, user: User) -> None:
    """Run the next tool call as ``user`` against the test session."""
    monkeypatch.setattr(server, "get_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(server, "resolve_current_user", lambda _session: user)


def test_list_suites_shapes_each_accessible_suite(db_session: Any, monkeypatch: Any) -> None:
    user = _user(db_session)
    suite = _suite(db_session, user)
    db_session.add(Check(suite_id=suite.id, name="c", expectation_type="expect_x", config={}))
    db_session.commit()
    _as(monkeypatch, db_session, user)

    out = server.list_suites()
    assert len(out) == 1
    assert out[0]["name"] == "Orders"
    assert out[0]["datasource"] == "snowflake"
    assert out[0]["env"] == "dev"
    assert out[0]["check_count"] == 1
    assert out[0]["last_run"] is None


def test_get_suite_results_returns_latest_run_per_check(db_session: Any, monkeypatch: Any) -> None:
    user = _user(db_session)
    suite = _suite(db_session, user)
    check = Check(suite_id=suite.id, name="not null email", expectation_type="expect_x", config={})
    db_session.add(check)
    run = Run(suite_id=suite.id, status="succeeded")
    db_session.add(run)
    db_session.flush()
    db_session.add(Result(run_id=run.id, check_id=check.id, status="fail"))
    db_session.commit()
    _as(monkeypatch, db_session, user)

    out = server.get_suite_results(str(suite.id))
    assert out["run"]["status"] == "succeeded"
    assert out["checks"][0]["name"] == "not null email"
    assert out["checks"][0]["status"] == "fail"


def test_get_suite_results_no_runs(db_session: Any, monkeypatch: Any) -> None:
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)
    out = server.get_suite_results(str(suite.id))
    assert out["run"] is None and out["checks"] == []


def test_get_suite_results_denied_for_inaccessible_suite(db_session: Any, monkeypatch: Any) -> None:
    owner = _user(db_session, "owner@acme.io")
    suite = _suite(db_session, owner)
    outsider = _user(db_session, "outsider@acme.io")
    _as(monkeypatch, db_session, outsider)
    with pytest.raises(ToolError):
        server.get_suite_results(str(suite.id))


def test_get_health_score_shape(db_session: Any, monkeypatch: Any) -> None:
    user = _user(db_session)
    _as(monkeypatch, db_session, user)
    out = server.get_health_score(window_days=7)
    assert out["window_days"] == 7
    assert {"health_score", "pass_rate", "total_runs", "active_connections", "trend"} <= out.keys()


def test_get_health_score_rejects_bad_window(db_session: Any, monkeypatch: Any) -> None:
    _as(monkeypatch, db_session, _user(db_session))
    with pytest.raises(ToolError):
        server.get_health_score(window_days=0)


def test_get_adf_pipeline_status_correlates_dq_run(db_session: Any, monkeypatch: Any) -> None:
    user = _user(db_session)
    suite = _suite(db_session, user)
    pr = PipelineRun(
        provider="adf",
        connection_id=suite.connection_id,
        provider_run_id="run-1",
        pipeline_or_dag_id="load_orders",
        env="dev",
        status="succeeded",
    )
    db_session.add(pr)
    dq = Run(suite_id=suite.id, status="succeeded", triggered_by="adf:load_orders:run-1")
    db_session.add(dq)
    db_session.commit()
    _as(monkeypatch, db_session, user)

    out = server.get_adf_pipeline_status()
    assert out[0]["pipeline"] == "load_orders"
    assert out[0]["dq_run"]["status"] == "succeeded"


def test_trigger_suite_run_queues_and_dispatches(db_session: Any, monkeypatch: Any) -> None:
    user = _user(db_session)
    suite = _suite(db_session, user)
    monkeypatch.setattr(run_dispatch, "dispatch_or_fail", lambda *a, **k: True)
    _as(monkeypatch, db_session, user)

    out = server.trigger_suite_run(str(suite.id))
    assert out["status"] == "queued"
    run = db_session.get(Run, uuid.UUID(out["run_id"]))
    assert run is not None and run.triggered_by == f"mcp:{user.id}"


def test_trigger_suite_run_rejects_targetless_suite(db_session: Any, monkeypatch: Any) -> None:
    user = _user(db_session)
    suite = _suite(db_session, user, with_target=False)
    _as(monkeypatch, db_session, user)
    with pytest.raises(ToolError):
        server.trigger_suite_run(str(suite.id))


def test_get_run_status_reports_progress(db_session: Any, monkeypatch: Any) -> None:
    user = _user(db_session)
    suite = _suite(db_session, user)
    check = Check(suite_id=suite.id, name="c", expectation_type="expect_x", config={})
    db_session.add(check)
    run = Run(suite_id=suite.id, status="running")
    db_session.add(run)
    db_session.commit()
    _as(monkeypatch, db_session, user)

    out = server.get_run_status(str(run.id))
    assert out["status"] == "running"
    assert out["total_checks"] == 1


def test_create_check_persists(db_session: Any, monkeypatch: Any) -> None:
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)

    out = server.create_check(
        str(suite.id),
        name="email not null",
        expectation_type="expect_column_values_to_not_be_null",
        config={"column": "email"},
    )
    persisted = db_session.get(Check, uuid.UUID(out["id"]))
    assert persisted is not None
    assert persisted.config == {"column": "email"}


def test_create_check_rejects_nul_bytes(db_session: Any, monkeypatch: Any) -> None:
    """NUL can't reach Postgres (#567) — the MCP boundary rejects it as a clean
    ToolError (mirroring the REST ApiModel guard), wherever it hides: the name
    or a nested config value."""
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)

    with pytest.raises(ToolError, match="NUL"):
        server.create_check(
            str(suite.id),
            name="evil-\x00-check",
            expectation_type="expect_column_values_to_not_be_null",
            config={"column": "email"},
        )
    with pytest.raises(ToolError, match="NUL"):
        server.create_check(
            str(suite.id),
            name="fine",
            expectation_type="expect_column_values_to_be_in_set",
            config={"column": "status", "value_set": ["ok", "bad\x00value"]},
        )


def test_create_check_requires_edit(db_session: Any, monkeypatch: Any) -> None:
    owner = _user(db_session, "owner@acme.io")
    suite = _suite(db_session, owner)
    _as(monkeypatch, db_session, _user(db_session, "outsider@acme.io"))
    with pytest.raises(ToolError):
        server.create_check(str(suite.id), name="x", expectation_type="expect_x")


def test_profile_column_shapes_result(db_session: Any, monkeypatch: Any) -> None:
    from backend.app.services.profile_service import ColumnProfile, ProfileResult

    user = _user(db_session)
    suite = _suite(db_session, user)
    fake = ProfileResult(
        row_count=100,
        table="ORDERS",
        schema="PUBLIC",
        catalog=None,
        path=None,
        file_format=None,
        columns=[
            ColumnProfile(
                column="revenue",
                null_count=2,
                null_fraction=0.02,
                distinct_count=98,
                min_value=1,
                max_value=999,
                top_values=[{"value": 1, "count": 5}],
            )
        ],
    )
    monkeypatch.setattr(profile_service, "profile_connection", lambda *a, **k: fake)
    _as(monkeypatch, db_session, user)

    out = server.profile_column(str(suite.id), columns=["revenue"], table="ORDERS")
    assert out["row_count"] == 100
    assert out["columns"][0]["column"] == "revenue"
    assert out["columns"][0]["null_count"] == 2


def test_bad_uuid_is_a_clean_tool_error(db_session: Any, monkeypatch: Any) -> None:
    _as(monkeypatch, db_session, _user(db_session))
    with pytest.raises(ToolError):
        server.get_suite_results("not-a-uuid")


# ── profile_column target defaulting (#583) ──────────────────────────────────


def test_profile_column_defaults_to_the_suites_run_target(
    db_session: Any, monkeypatch: Any
) -> None:
    """No explicit table/path → the suite's run target supplies them (#583)."""
    from backend.app.services.profile_service import ProfileResult

    user = _user(db_session)
    suite = _suite(db_session, user)  # target={"table": "ORDERS"}
    seen: dict[str, Any] = {}

    def _fake_profile(connection: Any, **kwargs: Any) -> ProfileResult:
        seen.update(kwargs)
        return ProfileResult(
            row_count=1,
            table=kwargs["table"],
            schema=kwargs["schema"],
            catalog=None,
            path=None,
            file_format=None,
            columns=[],
        )

    monkeypatch.setattr(profile_service, "profile_connection", _fake_profile)
    _as(monkeypatch, db_session, user)

    out = server.profile_column(str(suite.id), columns=["revenue"])
    assert seen["table"] == "ORDERS"
    assert seen["path"] is None
    assert out["table"] == "ORDERS"


def test_profile_column_explicit_table_still_wins(db_session: Any, monkeypatch: Any) -> None:
    from backend.app.services.profile_service import ProfileResult

    user = _user(db_session)
    suite = _suite(db_session, user)
    seen: dict[str, Any] = {}

    def _fake_profile(connection: Any, **kwargs: Any) -> ProfileResult:
        seen.update(kwargs)
        return ProfileResult(
            row_count=1,
            table=kwargs["table"],
            schema=None,
            catalog=None,
            path=None,
            file_format=None,
            columns=[],
        )

    monkeypatch.setattr(profile_service, "profile_connection", _fake_profile)
    _as(monkeypatch, db_session, user)

    server.profile_column(str(suite.id), columns=["x"], table="OTHER_TABLE")
    assert seen["table"] == "OTHER_TABLE"


def test_profile_column_no_target_anywhere_is_actionable_error(
    db_session: Any, monkeypatch: Any
) -> None:
    """422 path: no explicit table/path AND a targetless suite — the error says
    what to set instead of a bare validation failure."""
    user = _user(db_session)
    suite = _suite(db_session, user, with_target=False)
    _as(monkeypatch, db_session, user)

    with pytest.raises(ToolError, match="run target"):
        server.profile_column(str(suite.id), columns=["x"])


def test_profile_column_flatfile_target_defaults_path_and_format(
    db_session: Any, monkeypatch: Any
) -> None:
    from backend.app.services.profile_service import ProfileResult

    user = _user(db_session)
    conn = Connection(
        name=f"adls-{uuid.uuid4().hex[:8]}",
        type="adls_gen2",
        env="dev",
        config={"account_name": "acct", "container": "landing"},
        secret_ref="kv-adls",
        created_by=user.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(
        name="Logistics",
        connection_id=conn.id,
        created_by=user.id,
        target={"path": "logistics/tracking.csv", "file_format": "csv"},
    )
    db_session.add(suite)
    db_session.commit()
    seen: dict[str, Any] = {}

    def _fake_profile(connection: Any, **kwargs: Any) -> ProfileResult:
        seen.update(kwargs)
        return ProfileResult(
            row_count=1,
            table=None,
            schema=None,
            catalog=None,
            path=kwargs["path"],
            file_format=kwargs["file_format"],
            columns=[],
        )

    monkeypatch.setattr(profile_service, "profile_connection", _fake_profile)
    _as(monkeypatch, db_session, user)

    server.profile_column(str(suite.id), columns=["status"])
    assert seen["path"] == "logistics/tracking.csv"
    assert seen["file_format"] == "csv"
    assert seen["table"] is None
