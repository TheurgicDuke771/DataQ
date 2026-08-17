"""DB-backed tests for the MCP tools (real Postgres).

Each tool is a thin wrapper that opens a session, resolves the caller, and calls
the service layer with per-suite authz. We isolate the tool *logic* by patching
`server.get_session` → the test session and `server.resolve_current_user` → a
known user, then assert the returned LLM-shaped dict and that authz is enforced.
The auth/user-resolution itself is covered in test_mcp_auth.py. Skips without
TEST_DATABASE_URL.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from fastmcp.exceptions import ToolError
from sqlalchemy import select

from backend.app.db.models import Check, Connection, PipelineRun, Result, Run, Suite, User
from backend.app.mcp import server
from backend.app.services import (
    check_service,
    dryrun_service,
    profile_service,
    run_dispatch,
    run_service,
)


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


def test_list_suites_query_count_does_not_grow_with_suite_count(
    db_session: Any, monkeypatch: Any
) -> None:
    """`list_suites` must issue a FIXED number of queries, not O(suites) (#947).

    Asserted by counting SQL statements, because the obvious test — "the output
    is still correct" — passes just as happily with the N+1 in place. Only the
    query count distinguishes the two, and an LLM calling this tool cannot see
    the cost, so nothing else would ever surface a regression.

    Three per-suite queries used to run in the loop (connection, check count,
    latest run), so a 30-suite workspace issued ~90 round trips.
    """
    from sqlalchemy import event

    user = _user(db_session)
    for _ in range(4):
        suite = _suite(db_session, user)
        db_session.add(Check(suite_id=suite.id, name="c", expectation_type="expect_x", config={}))
    db_session.commit()
    _as(monkeypatch, db_session, user)

    statements: list[str] = []

    def _record(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        statements.append(statement)

    event.listen(db_session.bind, "before_cursor_execute", _record)
    try:
        out = server.list_suites()
    finally:
        event.remove(db_session.bind, "before_cursor_execute", _record)

    assert len(out) == 4
    assert {o["check_count"] for o in out} == {1}, "batching must not lose per-suite counts"
    # 4 suites, and the whole tool stays in single digits. The pre-fix code issued
    # 3 per suite on top of the listing, so this trips immediately if the loop
    # regains a query — while leaving room for the shared prelude to change.
    assert len(statements) <= 8, f"expected a fixed query count, issued {len(statements)}"


def test_list_suites_hides_unowned_suites_from_non_admin(db_session: Any, monkeypatch: Any) -> None:
    # Baseline for the admin case below: an outsider who is not a workspace-admin
    # sees none of another user's suites.
    owner = _user(db_session, "owner@acme.io")
    _suite(db_session, owner)
    outsider = _user(db_session, "outsider@acme.io")
    _as(monkeypatch, db_session, outsider)
    assert server.list_suites() == []


def test_list_suites_workspace_admin_sees_every_suite(
    db_session: Any, monkeypatch: Any, make_workspace_admin: Any
) -> None:
    # A workspace-admin driving DataQ over MCP gets the workspace-wide view (ADR
    # 0027), same as the REST list — even a suite they neither own nor share.
    owner = _user(db_session, "owner@acme.io")
    suite = _suite(db_session, owner)
    admin = _user(db_session, "admin@acme.io")
    make_workspace_admin(admin.email)
    _as(monkeypatch, db_session, admin)
    listed = {s["id"] for s in server.list_suites()}
    assert str(suite.id) in listed


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


def test_get_health_score_workspace_admin_aggregates_unowned_runs(
    db_session: Any, monkeypatch: Any, make_workspace_admin: Any
) -> None:
    # The aggregate honours the workspace-admin view (ADR 0027): a run on a suite
    # the caller doesn't own counts for an admin but not for a plain outsider.
    owner = _user(db_session, "owner@acme.io")
    suite = _suite(db_session, owner)
    db_session.add(Run(suite_id=suite.id, status="succeeded"))
    db_session.commit()

    outsider = _user(db_session, "outsider@acme.io")
    _as(monkeypatch, db_session, outsider)
    assert server.get_health_score()["total_runs"] == 0

    admin = _user(db_session, "admin@acme.io")
    make_workspace_admin(admin.email)
    _as(monkeypatch, db_session, admin)
    assert server.get_health_score()["total_runs"] >= 1


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


def _adf_run_on_unowned_suite(db_session: Any) -> User:
    """Seed a pipeline run correlated to a DQ run on a suite owned by someone
    else, and return a fresh outsider to view it. Shared by the admin +
    non-admin correlation-visibility tests below."""
    owner = _user(db_session, "owner@acme.io")
    suite = _suite(db_session, owner)
    db_session.add(
        PipelineRun(
            provider="adf",
            connection_id=suite.connection_id,
            provider_run_id="run-1",
            pipeline_or_dag_id="load_orders",
            env="dev",
            status="succeeded",
        )
    )
    db_session.add(Run(suite_id=suite.id, status="succeeded", triggered_by="adf:load_orders:run-1"))
    db_session.commit()
    return _user(db_session, "outsider@acme.io")


def test_get_adf_pipeline_status_hides_unowned_correlation_from_non_admin(
    db_session: Any, monkeypatch: Any
) -> None:
    # The pipeline run itself is workspace-wide, but the correlated DQ run is
    # scoped: a non-admin outsider sees the pipeline row with dq_run == None.
    outsider = _adf_run_on_unowned_suite(db_session)
    _as(monkeypatch, db_session, outsider)
    out = server.get_adf_pipeline_status()
    assert out[0]["pipeline"] == "load_orders"
    assert out[0]["dq_run"] is None


def test_get_adf_pipeline_status_workspace_admin_correlates_unowned_run(
    db_session: Any, monkeypatch: Any, make_workspace_admin: Any
) -> None:
    # A workspace-admin sees the correlated DQ run even on a suite they don't own
    # (ADR 0027 parity with the REST orchestration view).
    admin = _adf_run_on_unowned_suite(db_session)
    make_workspace_admin(admin.email)
    _as(monkeypatch, db_session, admin)
    out = server.get_adf_pipeline_status()
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


def test_create_check_rejects_oversized_name_or_type(db_session: Any, monkeypatch: Any) -> None:
    """The MCP tool has no Pydantic layer of its own, so `name`/`expectation_type`
    length is enforced by `check_service` — matching the REST `CheckCreate` bounds
    (256/128) and the `checks` column widths — as a clean ToolError, not a raw
    Postgres `StringDataRightTruncation` on the INSERT."""
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)

    with pytest.raises(ToolError, match="name"):
        server.create_check(
            str(suite.id),
            name="x" * 257,
            expectation_type="expect_column_values_to_not_be_null",
            config={"column": "email"},
        )
    with pytest.raises(ToolError, match="expectation_type"):
        server.create_check(
            str(suite.id),
            name="fine",
            expectation_type="x" * 129,
            config={"column": "email"},
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


def test_profile_column_top_n_is_bounded_like_the_rest_endpoint() -> None:
    """#327 review, P4: `top_n` is no longer only a result-size knob.

    The batched profiler materialises one rank row per `top_n` inside the
    statement, so an unbounded value — and LLM-generated arguments are exactly
    the ones that arrive unbounded — would compile a multi-megabyte query in the
    request thread. Asserted on the tool's advertised schema, because that is
    where FastMCP enforces it (and where the client reads it); calling the
    decorated function directly from Python bypasses validation entirely.
    """
    import asyncio

    tool = asyncio.run(server.mcp.get_tool("profile_column"))
    assert tool is not None
    assert tool.parameters["properties"]["top_n"] == {
        "default": 10,
        "minimum": 1,
        "maximum": 100,
        "type": "integer",
    }


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


def _iceberg_suite(db_session: Any, owner: User, *, target: dict[str, Any] | None = None) -> Suite:
    conn = Connection(
        name=f"ice-{uuid.uuid4().hex[:8]}",
        type="iceberg",
        env="dev",
        config={"catalog_type": "sql", "catalog_uri": "sqlite:///w"},
        secret_ref=None,
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(
        name="Iceberg Orders",
        connection_id=conn.id,
        created_by=owner.id,
        target=target if target is not None else {"table": "orders", "namespace": "sales"},
    )
    db_session.add(suite)
    db_session.commit()
    return suite


def test_profile_column_iceberg_default_profiles_the_folded_namespace_identifier(
    db_session: Any, monkeypatch: Any
) -> None:
    """No explicit table/path on an Iceberg suite → the run target's already-folded
    `namespace.table` identifier is used, and `namespace` is NOT passed a second
    time — `run_target.resolve_target` folded it into `table` already, so passing
    it again would double-fold (#721 code review)."""
    from backend.app.services.profile_service import ProfileResult

    user = _user(db_session)
    suite = _iceberg_suite(db_session, user)  # target={"table": "orders", "namespace": "sales"}
    seen: dict[str, Any] = {}

    def _fake_profile(connection: Any, **kwargs: Any) -> ProfileResult:
        seen.update(kwargs)
        return ProfileResult(row_count=1, table=kwargs["table"], columns=[])

    monkeypatch.setattr(profile_service, "profile_connection", _fake_profile)
    _as(monkeypatch, db_session, user)

    out = server.profile_column(str(suite.id), columns=["loaded_at"])
    assert seen["table"] == "sales.orders"  # already folded by resolve_target
    assert seen["namespace"] is None  # not re-passed — would double-fold
    assert out["table"] == "sales.orders"


def test_profile_column_iceberg_explicit_table_and_namespace_override(
    db_session: Any, monkeypatch: Any
) -> None:
    """An explicit `table` + `namespace` (profiling something other than the
    suite's own target) is passed straight through to the profiler, which does
    its own fold (#721 code review)."""
    from backend.app.services.profile_service import ProfileResult

    user = _user(db_session)
    suite = _iceberg_suite(db_session, user)
    seen: dict[str, Any] = {}

    def _fake_profile(connection: Any, **kwargs: Any) -> ProfileResult:
        seen.update(kwargs)
        return ProfileResult(row_count=1, table=kwargs["table"], columns=[])

    monkeypatch.setattr(profile_service, "profile_connection", _fake_profile)
    _as(monkeypatch, db_session, user)

    server.profile_column(str(suite.id), columns=["x"], table="other_table", namespace="other_ns")
    assert seen["table"] == "other_table"
    assert seen["namespace"] == "other_ns"


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


def _flatfile_suite(db_session: Any, user: User, target: dict[str, Any]) -> Suite:
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
    suite = Suite(name="Batchy", connection_id=conn.id, created_by=user.id, target=target)
    db_session.add(suite)
    db_session.commit()
    return suite


def test_profile_column_batch_target_materializes_the_concrete_file(
    db_session: Any, monkeypatch: Any
) -> None:
    """A flat-file *batch* target lists the store (like a run) and profiles the
    resolved concrete file."""
    from backend.app.datasources import flatfile
    from backend.app.services.profile_service import ProfileResult

    user = _user(db_session)
    suite = _flatfile_suite(
        db_session, user, {"pattern": r"tracking_([0-9]+)\.csv", "prefix": "logistics/"}
    )
    monkeypatch.setattr(
        flatfile, "resolve_batch_file", lambda **kw: "logistics/tracking_20260705.csv"
    )
    monkeypatch.setattr(
        server, "get_secret_store", lambda: SimpleNamespace(get=lambda ref: "sas-token")
    )
    seen: dict[str, Any] = {}

    def _fake_profile(connection: Any, **kwargs: Any) -> ProfileResult:
        seen.update(kwargs)
        return ProfileResult(
            row_count=1,
            table=None,
            schema=None,
            catalog=None,
            path=kwargs["path"],
            file_format=None,
            columns=[],
        )

    monkeypatch.setattr(profile_service, "profile_connection", _fake_profile)
    _as(monkeypatch, db_session, user)

    server.profile_column(str(suite.id), columns=["status"])
    assert seen["path"] == "logistics/tracking_20260705.csv"
    assert seen["table"] is None


def test_profile_column_batch_target_no_file_yet_is_actionable(
    db_session: Any, monkeypatch: Any
) -> None:
    from backend.app.datasources import flatfile

    user = _user(db_session)
    suite = _flatfile_suite(
        db_session, user, {"pattern": r"tracking_([0-9]+)\.csv", "prefix": "logistics/"}
    )

    def _no_match(**kw: Any) -> str:
        raise flatfile.BatchNotFoundError("no object matched pattern")

    monkeypatch.setattr(flatfile, "resolve_batch_file", _no_match)
    monkeypatch.setattr(
        server, "get_secret_store", lambda: SimpleNamespace(get=lambda ref: "sas-token")
    )
    _as(monkeypatch, db_session, user)

    with pytest.raises(ToolError, match="matched no file"):
        server.profile_column(str(suite.id), columns=["status"])


def test_profile_column_uc_target_defaults_catalog(db_session: Any, monkeypatch: Any) -> None:
    from backend.app.services.profile_service import ProfileResult

    user = _user(db_session)
    conn = Connection(
        name=f"uc-{uuid.uuid4().hex[:8]}",
        type="unity_catalog",
        env="dev",
        config={"host": "h", "http_path": "/sql/1"},
        secret_ref="kv-uc",
        created_by=user.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(
        name="UC",
        connection_id=conn.id,
        created_by=user.id,
        target={"table": "orders", "schema": "gold", "catalog": "dataq_retail"},
    )
    db_session.add(suite)
    db_session.commit()
    seen: dict[str, Any] = {}

    def _fake_profile(connection: Any, **kwargs: Any) -> ProfileResult:
        seen.update(kwargs)
        return ProfileResult(
            row_count=1,
            table=kwargs["table"],
            schema=kwargs["schema"],
            catalog=kwargs["catalog"],
            path=None,
            file_format=None,
            columns=[],
        )

    monkeypatch.setattr(profile_service, "profile_connection", _fake_profile)
    _as(monkeypatch, db_session, user)

    server.profile_column(str(suite.id), columns=["amount"])
    assert (seen["table"], seen["schema"], seen["catalog"]) == ("orders", "gold", "dataq_retail")


def test_profile_column_explicit_path_wins_over_target(db_session: Any, monkeypatch: Any) -> None:
    from backend.app.services.profile_service import ProfileResult

    user = _user(db_session)
    suite = _flatfile_suite(db_session, user, {"path": "logistics/saved.csv", "file_format": "csv"})
    seen: dict[str, Any] = {}

    def _fake_profile(connection: Any, **kwargs: Any) -> ProfileResult:
        seen.update(kwargs)
        return ProfileResult(
            row_count=1,
            table=None,
            schema=None,
            catalog=None,
            path=kwargs["path"],
            file_format=None,
            columns=[],
        )

    monkeypatch.setattr(profile_service, "profile_connection", _fake_profile)
    _as(monkeypatch, db_session, user)

    server.profile_column(str(suite.id), columns=["x"], path="logistics/other.csv")
    assert seen["path"] == "logistics/other.csv"


# ─────────────────────── /mcp mount: DNS-rebind Host guard (#672 fastmcp bump) ──


def test_build_mcp_app_allowlists_proxied_hosts(monkeypatch: Any) -> None:
    """FastMCP's transport guard defaults to loopback-only hosts and 421s anything
    else — but DataQ always fronts the api with the nginx proxy, which forwards the
    upstream Host (the ACA FQDN in prod, `api` in compose). Assert build_mcp_app
    passes those hosts so the guard can't shadow the real auth gate."""
    captured: dict[str, Any] = {}
    monkeypatch.setattr(server, "mcp_enabled", lambda: True)
    monkeypatch.setattr(server.mcp, "http_app", lambda **kw: captured.update(kw) or "APP")

    assert server.build_mcp_app() == "APP"
    hosts = captured["allowed_hosts"]
    assert "*.azurecontainerapps.io" in hosts  # prod (internal api FQDN)
    assert "api" in hosts  # docker-compose upstream
    # Origins are relaxed for the same reason (Bearer-token auth → no CSRF), so a
    # browser client (claude.ai) isn't 403'd by the guard's separate Origin check.
    assert captured["allowed_origins"] == ["*"]


def test_allowed_hosts_match_prod_and_compose_upstreams() -> None:
    """Prove the allowlist patterns actually match the real proxied Host values —
    so the mount doesn't 421 in prod (regression guard for the 3.4.3 DNS-rebind
    guard). Uses stdlib `fnmatchcase`, exactly what FastMCP's `_host_matches` uses,
    without coupling the test to that private helper."""
    from fnmatch import fnmatchcase

    def matches(host: str, patterns: list[str]) -> bool:
        return any(p == "*" or fnmatchcase(host, p) for p in patterns)

    hosts = ["*.azurecontainerapps.io", "api", "localhost", "127.0.0.1"]
    # The SHAPE of the upstream Host nginx forwards (DATAQ_API_UPSTREAM, internal
    # ingress): `<app>.internal.<env-hash>.<region>.azurecontainerapps.io`. Synthetic
    # on purpose — what's under test is that fnmatch `*` spans dots and so matches a
    # multi-label FQDN, not any one deployment's name (#730).
    assert matches("dataq-app-api.internal.example-0a1b2c3d.westus2.azurecontainerapps.io", hosts)
    assert matches("api", hosts)  # docker-compose
    assert not matches("evil.example.com", hosts)  # still rejects the rest


def test_allowed_hosts_come_from_settings_not_hardcoded(monkeypatch: Any) -> None:
    """A non-ACA deployment can configure the allowlist (#728).

    The list used to be a literal in `build_mcp_app`, which is the one deploy-target
    coupling ADR 0010/0013 forbids in app code: any proxy forwarding a different
    upstream Host (EKS/GKE, on-prem, a renamed compose service) got a 421 with no
    way to fix it short of a code change.
    """
    from backend.app.core import config

    captured: dict[str, Any] = {}
    monkeypatch.setattr(server, "mcp_enabled", lambda: True)
    monkeypatch.setattr(server.mcp, "http_app", lambda **kw: captured.update(kw) or "APP")

    settings = config.get_settings()
    monkeypatch.setattr(settings, "mcp_allowed_hosts", "*.example.internal, dataq-api ")
    config.get_settings.cache_clear()
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(server, "get_settings", lambda: settings)

    server.build_mcp_app()

    # Whitespace stripped, order preserved, and the ACA default fully replaced —
    # not merged, so an operator can genuinely narrow the allowlist.
    assert captured["allowed_hosts"] == ["*.example.internal", "dataq-api"]


def test_allowed_hosts_default_to_the_aca_shape_when_unset(monkeypatch: Any) -> None:
    """Empty config keeps the deployed behaviour untouched (#728).

    The setting is opt-in: an existing deployment that sets nothing must get the
    same list the literal used to hardcode, or this refactor silently 421s prod.
    """
    from backend.app.core import config

    settings = config.get_settings()
    monkeypatch.setattr(settings, "mcp_allowed_hosts", "")
    assert settings.mcp_allowed_host_list == [
        "*.azurecontainerapps.io",
        "api",
        "localhost",
        "127.0.0.1",
    ]


def test_get_suite_results_reports_whether_a_check_saw_a_sample(
    db_session: Any, monkeypatch: Any
) -> None:
    """#595 C8. Without this field an AI client reads a green board drawn from a
    2% sample and confidently reports full-dataset quality — the #424/#1115
    overclaim class, reintroduced for every MCP consumer at once."""
    user = _user(db_session)
    suite = _suite(db_session, user)
    check = Check(suite_id=suite.id, name="not null id", expectation_type="expect_x", config={})
    db_session.add(check)
    run = Run(suite_id=suite.id, status="succeeded")
    db_session.add(run)
    db_session.flush()
    record = {
        "strategy": "random",
        "requested_rows": 100_000,
        "rows": 100_000,
        "total_rows": 5_000_000,
        "sampled": True,
    }
    db_session.add(Result(run_id=run.id, check_id=check.id, status="pass", sampling=record))
    db_session.commit()
    _as(monkeypatch, db_session, user)

    assert server.get_suite_results(str(suite.id))["checks"][0]["sampling"] == record


def test_get_suite_results_reports_null_sampling_for_a_complete_read(
    db_session: Any, monkeypatch: Any
) -> None:
    """`null`, not an object claiming `sampled: false` — the same shape a client
    sees for every result written before scale-aware execution existed, so it can
    branch on presence without a backfill."""
    user = _user(db_session)
    suite = _suite(db_session, user)
    check = Check(suite_id=suite.id, name="c", expectation_type="expect_x", config={})
    db_session.add(check)
    run = Run(suite_id=suite.id, status="succeeded")
    db_session.add(run)
    db_session.flush()
    db_session.add(Result(run_id=run.id, check_id=check.id, status="pass"))
    db_session.commit()
    _as(monkeypatch, db_session, user)

    assert server.get_suite_results(str(suite.id))["checks"][0]["sampling"] is None


# ───────────────────── Tier-1 check reads (#529) ──────────────────────────


def _check(db_session: Any, suite: Suite, **kw: Any) -> Check:
    defaults: dict[str, Any] = {
        "suite_id": suite.id,
        "name": "not null email",
        "expectation_type": "expect_column_values_to_not_be_null",
        "config": {"column": "EMAIL"},
    }
    check = Check(**{**defaults, **kw})
    db_session.add(check)
    db_session.commit()
    return check


def test_list_checks_shapes_every_check_on_the_suite(db_session: Any, monkeypatch: Any) -> None:
    user = _user(db_session)
    suite = _suite(db_session, user)
    _check(db_session, suite, dimension="completeness", warn_threshold=Decimal("0.01"))
    _as(monkeypatch, db_session, user)

    out = server.list_checks(str(suite.id))
    assert out["total"] == 1 and out["truncated"] is False
    (check,) = out["checks"]
    assert check["name"] == "not null email"
    assert check["kind"] == "expectation"
    assert check["expectation_type"] == "expect_column_values_to_not_be_null"
    assert check["dimension"] == "completeness"
    # `config` is what lets an LLM say WHICH column the check covers.
    assert check["config"] == {"column": "EMAIL"}
    assert check["warn_threshold"] == 0.01
    assert check["fail_threshold"] is None
    assert check["alert_snoozed_until"] is None
    assert check["source_connection_id"] is None


def test_list_checks_surfaces_a_live_alert_snooze(db_session: Any, monkeypatch: Any) -> None:
    """A snoozed check still runs and still fails — it just alerts nobody (#370).
    An LLM asked "why was there no alert?" can only answer if it can see this."""
    user = _user(db_session)
    suite = _suite(db_session, user)
    until = datetime.now(UTC) + timedelta(hours=4)
    _check(db_session, suite, alert_snoozed_until=until)
    _as(monkeypatch, db_session, user)

    snoozed = server.list_checks(str(suite.id))["checks"][0]["alert_snoozed_until"]
    assert snoozed == until.isoformat()


def test_list_checks_denied_for_inaccessible_suite(db_session: Any, monkeypatch: Any) -> None:
    owner = _user(db_session, "owner@acme.io")
    suite = _suite(db_session, owner)
    _check(db_session, suite)
    _as(monkeypatch, db_session, _user(db_session, "outsider@acme.io"))
    with pytest.raises(ToolError):
        server.list_checks(str(suite.id))


def test_get_check_returns_the_full_definition(db_session: Any, monkeypatch: Any) -> None:
    user = _user(db_session)
    suite = _suite(db_session, user)
    check = _check(db_session, suite, fail_threshold=Decimal("0.05"))
    _as(monkeypatch, db_session, user)

    out = server.get_check(str(suite.id), str(check.id))
    assert out["id"] == str(check.id)
    assert out["suite_id"] == str(suite.id)
    assert out["fail_threshold"] == 0.05
    assert out["created_at"] and out["updated_at"]


def test_get_check_from_another_suite_is_not_found(db_session: Any, monkeypatch: Any) -> None:
    """The cross-suite guard is the whole reason `get_check` takes BOTH ids: a
    check id from a suite the caller cannot see must not resolve by passing a
    suite they can (the 404-no-leak discipline, ADR 0027)."""
    user = _user(db_session)
    mine = _suite(db_session, user)
    theirs = _suite(db_session, _user(db_session, "owner@acme.io"))
    foreign = _check(db_session, theirs)
    _as(monkeypatch, db_session, user)

    with pytest.raises(ToolError):
        server.get_check(str(mine.id), str(foreign.id))


def test_get_check_denied_for_inaccessible_suite(db_session: Any, monkeypatch: Any) -> None:
    owner = _user(db_session, "owner@acme.io")
    suite = _suite(db_session, owner)
    check = _check(db_session, suite)
    _as(monkeypatch, db_session, _user(db_session, "outsider@acme.io"))
    with pytest.raises(ToolError):
        server.get_check(str(suite.id), str(check.id))


def test_get_check_history_is_chronological_with_metrics(db_session: Any, monkeypatch: Any) -> None:
    user = _user(db_session)
    suite = _suite(db_session, user)
    check = _check(db_session, suite)
    base = datetime.now(UTC) - timedelta(hours=3)
    for offset, (status, metric) in enumerate(
        [("pass", 100), ("fail", 4), ("pass", 98)],
    ):
        run = Run(suite_id=suite.id, status="succeeded", created_at=base + timedelta(hours=offset))
        db_session.add(run)
        db_session.flush()
        db_session.add(
            Result(
                run_id=run.id,
                check_id=check.id,
                status=status,
                metric_value=Decimal(metric),
            )
        )
    db_session.commit()
    _as(monkeypatch, db_session, user)

    points = server.get_check_history(str(suite.id), str(check.id))["points"]
    # Oldest first — the order a trend is read in, and the opposite of the SQL.
    assert [p["status"] for p in points] == ["pass", "fail", "pass"]
    assert [p["metric_value"] for p in points] == [100.0, 4.0, 98.0]
    assert points[0]["run_id"] and points[0]["at"]


def test_get_check_history_limit_keeps_the_most_recent_window(
    db_session: Any, monkeypatch: Any
) -> None:
    """`limit` must trim the OLD end, not the new one: a caller asking for 2
    points wants the last two runs, not the first two (`list_check_result_history`
    takes newest-first in SQL and reverses)."""
    user = _user(db_session)
    suite = _suite(db_session, user)
    check = _check(db_session, suite)
    base = datetime.now(UTC) - timedelta(hours=3)
    for offset, metric in enumerate([1, 2, 3]):
        run = Run(suite_id=suite.id, status="succeeded", created_at=base + timedelta(hours=offset))
        db_session.add(run)
        db_session.flush()
        db_session.add(
            Result(run_id=run.id, check_id=check.id, status="pass", metric_value=Decimal(metric))
        )
    db_session.commit()
    _as(monkeypatch, db_session, user)

    points = server.get_check_history(str(suite.id), str(check.id), limit=2)["points"]
    assert [p["metric_value"] for p in points] == [2.0, 3.0]


def test_get_check_history_limit_is_bounded_in_the_tool_schema() -> None:
    """The bound must be visible to the CLIENT, not just enforced server-side —
    an LLM picks its argument from the schema (the `profile_column` top_n rule).
    Asserted on the advertised schema, since calling the decorated function
    directly from Python bypasses FastMCP's validation entirely."""
    import asyncio

    tool = asyncio.run(server.mcp.get_tool("get_check_history"))
    assert tool is not None
    assert tool.parameters["properties"]["limit"] == {
        "default": 30,
        "minimum": 1,
        "maximum": 200,
        "type": "integer",
    }


def test_get_check_history_denied_for_inaccessible_suite(db_session: Any, monkeypatch: Any) -> None:
    owner = _user(db_session, "owner@acme.io")
    suite = _suite(db_session, owner)
    check = _check(db_session, suite)
    _as(monkeypatch, db_session, _user(db_session, "outsider@acme.io"))
    with pytest.raises(ToolError):
        server.get_check_history(str(suite.id), str(check.id))


def test_list_checks_reports_an_expired_snooze_as_not_snoozed(
    db_session: Any, monkeypatch: Any
) -> None:
    """`snooze_check` never clears the column, so a raw pass-through would show a
    month-old timestamp on the one question ("why was there no alert?") where a
    wrong answer is most confidently wrong. `suppression` compares against now;
    so does this."""
    user = _user(db_session)
    suite = _suite(db_session, user)
    _check(db_session, suite, alert_snoozed_until=datetime.now(UTC) - timedelta(days=30))
    _as(monkeypatch, db_session, user)

    assert server.list_checks(str(suite.id))["checks"][0]["alert_snoozed_until"] is None


def test_list_checks_reports_a_comparison_checks_baseline_connection(
    db_session: Any, monkeypatch: Any
) -> None:
    """Non-NULL exactly for `kind='comparison'` (a table CHECK enforces it): an
    LLM that cannot see it can describe the rule but not what it compares
    against."""
    user = _user(db_session)
    suite = _suite(db_session, user)
    baseline = _suite(db_session, user).connection_id
    _check(
        db_session,
        suite,
        kind="comparison",
        expectation_type="comparison:records",
        source_connection_id=baseline,
        config={"source": {"table": "ORDERS"}, "keys": ["ID"]},
    )
    _as(monkeypatch, db_session, user)

    (check,) = server.list_checks(str(suite.id))["checks"]
    assert check["source_connection_id"] == str(baseline)
    assert server.get_check(str(suite.id), check["id"])["source_connection_id"] == str(baseline)


def test_list_checks_truncation_is_visible_not_silent(db_session: Any, monkeypatch: Any) -> None:
    """A bare truncated list would let an LLM report "the suite has 2 checks"
    with total confidence. `total` + `truncated` make the cut observable."""
    user = _user(db_session)
    suite = _suite(db_session, user)
    for n in range(3):
        _check(db_session, suite, name=f"check {n}")
    _as(monkeypatch, db_session, user)

    out = server.list_checks(str(suite.id), limit=2)
    assert len(out["checks"]) == 2
    assert out["total"] == 3
    assert out["truncated"] is True


def test_list_checks_limit_is_bounded_in_the_tool_schema() -> None:
    import asyncio

    tool = asyncio.run(server.mcp.get_tool("list_checks"))
    assert tool is not None
    assert tool.parameters["properties"]["limit"] == {
        "default": 200,
        "minimum": 1,
        "maximum": 500,
        "type": "integer",
    }


# ───────────────────── Tier-1 run reads (#529) ────────────────────────────


def _run_with_results(
    db_session: Any,
    suite: Suite,
    *,
    status: str = "succeeded",
    outcomes: tuple[str, ...] = ("pass",),
    created_at: Any = None,
) -> Run:
    run = Run(suite_id=suite.id, status=status)
    if created_at is not None:
        run.created_at = created_at
    db_session.add(run)
    db_session.flush()
    for n, outcome in enumerate(outcomes):
        check = Check(
            suite_id=suite.id, name=f"c{n}", expectation_type="expect_x", config={"column": "AMT"}
        )
        db_session.add(check)
        db_session.flush()
        db_session.add(Result(run_id=run.id, check_id=check.id, status=outcome))
    db_session.commit()
    return run


def test_list_runs_reports_execution_status_and_dq_outcome_separately(
    db_session: Any, monkeypatch: Any
) -> None:
    """The distinction the tool docstring makes must be real in the payload: a run
    is `succeeded` when DataQ executed it, even when every check inside failed.
    Merging the two is the misreport this shape exists to prevent."""
    user = _user(db_session)
    suite = _suite(db_session, user)
    _run_with_results(db_session, suite, status="succeeded", outcomes=("pass", "fail", "critical"))
    _as(monkeypatch, db_session, user)

    out = server.list_runs()
    assert out["total"] == 1
    (run,) = out["runs"]
    assert run["status"] == "succeeded"
    assert run["results_final"] is True
    assert run["checks_total"] == 3
    assert run["checks_passed"] == 1
    assert run["worst_severity"] == "critical"
    assert run["suite_id"] == str(suite.id)


def test_list_runs_excludes_operational_results_from_the_denominator(
    db_session: Any, monkeypatch: Any
) -> None:
    """`skip`/`error` are not evaluated checks (#122), so an all-skip run must
    report 0 total — not a misleading `0/N` that reads as total failure."""
    user = _user(db_session)
    suite = _suite(db_session, user)
    _run_with_results(db_session, suite, outcomes=("skip", "error"))
    _as(monkeypatch, db_session, user)

    (run,) = server.list_runs()["runs"]
    assert run["checks_total"] == 0
    assert run["worst_severity"] is None


def test_list_runs_total_is_independent_of_the_page(db_session: Any, monkeypatch: Any) -> None:
    """A short page must not be mistaken for the end of the list (#1108): `total`
    counts the whole matching population, not the slice."""
    user = _user(db_session)
    suite = _suite(db_session, user)
    for _ in range(3):
        _run_with_results(db_session, suite)
    _as(monkeypatch, db_session, user)

    out = server.list_runs(limit=1)
    assert len(out["runs"]) == 1
    assert out["total"] == 3


def test_list_runs_hides_runs_on_inaccessible_suites(db_session: Any, monkeypatch: Any) -> None:
    owner = _user(db_session, "owner@acme.io")
    suite = _suite(db_session, owner)
    _run_with_results(db_session, suite)
    _as(monkeypatch, db_session, _user(db_session, "outsider@acme.io"))

    out = server.list_runs()
    assert out["runs"] == [] and out["total"] == 0


def test_list_runs_named_inaccessible_suite_errors_rather_than_returning_empty(
    db_session: Any, monkeypatch: Any
) -> None:
    """An empty answer must never stand in for "you may not ask" (#828) — and the
    denial must not confirm the suite exists either."""
    owner = _user(db_session, "owner@acme.io")
    suite = _suite(db_session, owner)
    _run_with_results(db_session, suite)
    _as(monkeypatch, db_session, _user(db_session, "outsider@acme.io"))

    with pytest.raises(ToolError):
        server.list_runs(suite_id=str(suite.id))


def test_list_runs_rejects_a_status_outside_the_vocabulary(
    db_session: Any, monkeypatch: Any
) -> None:
    """A typo'd status must raise, not return a confident empty list that reads as
    "no runs in that state" (#828)."""
    user = _user(db_session)
    _suite(db_session, user)
    _as(monkeypatch, db_session, user)

    with pytest.raises(ToolError):
        server.list_runs(status="suceeded")


def test_list_runs_workspace_admin_sees_unowned_runs(
    db_session: Any, monkeypatch: Any, make_workspace_admin: Any
) -> None:
    owner = _user(db_session, "owner@acme.io")
    suite = _suite(db_session, owner)
    run = _run_with_results(db_session, suite)
    admin = _user(db_session, "admin@acme.io")
    make_workspace_admin(admin.email)
    _as(monkeypatch, db_session, admin)

    assert str(run.id) in {r["id"] for r in server.list_runs()["runs"]}


def test_get_run_results_returns_that_runs_checks(db_session: Any, monkeypatch: Any) -> None:
    """A *named* run, not the latest one — the whole point of the tool is reading
    history that `get_suite_results` has already moved past."""
    user = _user(db_session)
    suite = _suite(db_session, user)
    older = _run_with_results(
        db_session,
        suite,
        outcomes=("fail",),
        created_at=datetime.now(UTC) - timedelta(days=2),
    )
    _run_with_results(db_session, suite, outcomes=("pass",))
    _as(monkeypatch, db_session, user)

    out = server.get_run_results(str(older.id))
    assert out["run"]["id"] == str(older.id)
    assert out["run"]["results_final"] is True
    assert [c["status"] for c in out["checks"]] == ["fail"]
    # And `get_suite_results` still reports the newer one, so the two really do
    # answer different questions.
    assert server.get_suite_results(str(suite.id))["checks"][0]["status"] == "pass"


def test_get_run_results_withholds_an_incomplete_runs_partial_results(
    db_session: Any, monkeypatch: Any
) -> None:
    """The #318 rule, inherited from the shared payload builder rather than
    re-implemented: results are committed per phase, so a `running` run holds a
    genuine fraction of the suite — which an LLM would summarise as the answer."""
    user = _user(db_session)
    suite = _suite(db_session, user)
    run = _run_with_results(db_session, suite, status="running", outcomes=("fail",))
    _as(monkeypatch, db_session, user)

    out = server.get_run_results(str(run.id))
    assert out["run"]["results_final"] is False
    assert out["checks"] == []


def test_get_run_results_denied_for_a_run_on_an_inaccessible_suite(
    db_session: Any, monkeypatch: Any
) -> None:
    """A run id alone must not be a way around suite scoping."""
    owner = _user(db_session, "owner@acme.io")
    suite = _suite(db_session, owner)
    run = _run_with_results(db_session, suite)
    _as(monkeypatch, db_session, _user(db_session, "outsider@acme.io"))

    with pytest.raises(ToolError):
        server.get_run_results(str(run.id))


def test_get_run_results_unknown_run_is_a_clean_error(db_session: Any, monkeypatch: Any) -> None:
    _as(monkeypatch, db_session, _user(db_session))
    with pytest.raises(ToolError):
        server.get_run_results(str(uuid.uuid4()))


def test_list_runs_bounds_are_advertised_in_the_tool_schema() -> None:
    import asyncio

    tool = asyncio.run(server.mcp.get_tool("list_runs"))
    assert tool is not None
    props = tool.parameters["properties"]
    assert props["limit"] == {"default": 20, "minimum": 1, "maximum": 200, "type": "integer"}
    assert props["offset"]["minimum"] == 0


def test_list_runs_withholds_a_mid_run_suites_partial_outcome(
    db_session: Any, monkeypatch: Any
) -> None:
    """The aggregate overclaims exactly the way the row list does (#318).

    A suite 2 checks into 30 has a real, all-passing partial set, which
    `check_outcome_counts` faithfully reports as `2 / 2, worst_severity: null` —
    the tool's own definition of "nothing failed", asserted about a run that has
    barely started. The REST table survives this because a "running" badge sits
    beside the numbers; an LLM has no badge, only fields.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _run_with_results(db_session, suite, status="running", outcomes=("pass", "pass"))
    _as(monkeypatch, db_session, user)

    (run,) = server.list_runs()["runs"]
    assert run["status"] == "running"
    assert run["results_final"] is False
    assert run["checks_total"] is None
    assert run["checks_passed"] is None
    assert run["worst_severity"] is None


# ─────────────── Tier-1 ops/config reads (#529) ───────────────────────────


def test_list_connections_returns_metadata_and_health_but_never_config(
    db_session: Any, monkeypatch: Any
) -> None:
    """The #529 constraint, asserted as an absence rather than assumed.

    `config` carries account identifiers, hosts, paths and `*_secret_name`
    references (#1118). Names, types and health answer every question about
    connections; this is the one surface where the rest would be handed to a
    model verbatim, so it must not appear at all — nor may `secret_ref`.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)

    (conn,) = [c for c in server.list_connections() if c["id"] == str(suite.connection_id)]
    assert conn["type"] == "snowflake"
    assert conn["env"] == "dev"
    assert conn["has_secret"] is True
    assert "config" not in conn
    assert "secret_ref" not in conn
    # And nothing FROM the config leaked under another key — asserted against the
    # stored config's own values, so a future field that grafts one in trips here
    # rather than being caught by a name check that only knows today's names.
    stored = db_session.get(Connection, suite.connection_id)
    leaked = set(stored.config.values()) & {v for v in conn.values() if isinstance(v, str)}
    assert not leaked, leaked


def test_list_connections_reports_unknown_health_as_null_not_healthy(
    db_session: Any, monkeypatch: Any
) -> None:
    """A connection with no runs is *unknown*, and the docstring says so. Reading
    a null as reassurance is the #828 blindness in a new place."""
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)

    (conn,) = [c for c in server.list_connections() if c["id"] == str(suite.connection_id)]
    assert conn["last_run_at"] is None
    assert conn["last_run_error"] is None
    # `None`, not 0 — a never-run connection is unknown, and a concrete zero is
    # the reassurance the docstring forbids.
    assert conn["consecutive_run_failures"] is None
    # Same distinction on the credential: null expiry could mean "no readable
    # lifetime" OR "never looked", and only this field tells them apart (#1024).
    assert conn["credential_expires_at"] is None
    assert conn["credential_expiry_checked_at"] is None


def test_list_connections_surfaces_a_failing_datasource(db_session: Any, monkeypatch: Any) -> None:
    """The #954 signal: a dead credential is invisible until a run fails, and then
    it shows on the run, not the connection."""
    user = _user(db_session)
    suite = _suite(db_session, user)
    db_session.add(Run(suite_id=suite.id, status="failed", failure_reason="authentication failed"))
    db_session.commit()
    _as(monkeypatch, db_session, user)

    (conn,) = [c for c in server.list_connections() if c["id"] == str(suite.connection_id)]
    assert conn["consecutive_run_failures"] == 1
    assert conn["last_run_error"] == "authentication failed"


def test_list_connections_filters_by_type_and_env(db_session: Any, monkeypatch: Any) -> None:
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)

    assert any(
        c["id"] == str(suite.connection_id) for c in server.list_connections(type="snowflake")
    )
    assert not any(c["id"] == str(suite.connection_id) for c in server.list_connections(type="s3"))
    assert not any(c["id"] == str(suite.connection_id) for c in server.list_connections(env="uat"))


def _schedule(db_session: Any, suite: Suite, owner: User, **kw: Any) -> Any:
    from backend.app.db.models import Schedule

    defaults: dict[str, Any] = {
        "suite_id": suite.id,
        "cron": "0 2 * * *",
        "timezone": "UTC",
        "next_run_at": datetime.now(UTC) + timedelta(hours=1),
        "created_by": owner.id,
    }
    sched = Schedule(**{**defaults, **kw})
    db_session.add(sched)
    db_session.commit()
    return sched


def test_list_schedules_shapes_the_schedule(db_session: Any, monkeypatch: Any) -> None:
    user = _user(db_session)
    suite = _suite(db_session, user)
    _schedule(db_session, suite, user)
    _as(monkeypatch, db_session, user)

    (sched,) = server.list_schedules()
    assert sched["suite_id"] == str(suite.id)
    assert sched["cron"] == "0 2 * * *"
    assert sched["timezone"] == "UTC"
    assert sched["enabled"] is True
    assert sched["last_run_at"] is None


def test_list_schedules_includes_disabled_ones_and_says_so(
    db_session: Any, monkeypatch: Any
) -> None:
    """A disabled schedule still reads back — the `enabled` flag is the answer, not
    the row's presence. Filtering must be explicit."""
    user = _user(db_session)
    suite = _suite(db_session, user)
    _schedule(db_session, suite, user, enabled=False)
    _as(monkeypatch, db_session, user)

    assert server.list_schedules()[0]["enabled"] is False
    assert server.list_schedules(enabled=True) == []
    assert len(server.list_schedules(enabled=False)) == 1


def test_list_schedules_hides_schedules_on_inaccessible_suites(
    db_session: Any, monkeypatch: Any
) -> None:
    owner = _user(db_session, "owner@acme.io")
    suite = _suite(db_session, owner)
    _schedule(db_session, suite, owner)
    _as(monkeypatch, db_session, _user(db_session, "outsider@acme.io"))
    assert server.list_schedules() == []


def test_list_schedules_named_inaccessible_suite_errors(db_session: Any, monkeypatch: Any) -> None:
    owner = _user(db_session, "owner@acme.io")
    suite = _suite(db_session, owner)
    _as(monkeypatch, db_session, _user(db_session, "outsider@acme.io"))
    with pytest.raises(ToolError):
        server.list_schedules(suite_id=str(suite.id))


def _binding(db_session: Any, suite: Suite, _owner: User, **kw: Any) -> Any:
    from backend.app.db.models import TriggerBinding

    defaults: dict[str, Any] = {
        "suite_id": suite.id,
        "provider": "adf",
        "pipeline_or_dag_id": "pl_nightly_load",
        "env": "dev",
    }
    binding = TriggerBinding(**{**defaults, **kw})
    db_session.add(binding)
    db_session.commit()
    return binding


def test_list_trigger_bindings_shapes_the_binding(db_session: Any, monkeypatch: Any) -> None:
    user = _user(db_session)
    suite = _suite(db_session, user)
    _binding(db_session, suite, user)
    _as(monkeypatch, db_session, user)

    (b,) = server.list_trigger_bindings()
    assert b["provider"] == "adf"
    assert b["pipeline_or_dag_id"] == "pl_nightly_load"
    assert b["env"] == "dev"
    assert b["suite_id"] == str(suite.id)
    assert b["enabled"] is True


def test_list_trigger_bindings_rejects_an_unknown_provider(
    db_session: Any, monkeypatch: Any
) -> None:
    """All three providers are valid (ADR 0029 added dbt) — and a typo must raise
    rather than return an empty list that reads as "nothing is wired up"."""
    user = _user(db_session)
    _as(monkeypatch, db_session, user)
    with pytest.raises(ToolError):
        server.list_trigger_bindings(provider="databricks")


def test_list_trigger_bindings_hides_bindings_on_inaccessible_suites(
    db_session: Any, monkeypatch: Any
) -> None:
    owner = _user(db_session, "owner@acme.io")
    suite = _suite(db_session, owner)
    _binding(db_session, suite, owner)
    _as(monkeypatch, db_session, _user(db_session, "outsider@acme.io"))
    assert server.list_trigger_bindings() == []


def test_get_notification_config_defaults_when_unconfigured(
    db_session: Any, monkeypatch: Any
) -> None:
    """A suite with no config still alerts on the defaults. Reporting "not
    configured" as "alerts are off" would be the wrong answer to the question the
    tool is actually asked."""
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)

    out = server.get_notification_config(str(suite.id))
    assert out["configured"] is False
    assert out["enabled"] is True
    assert out["alert_on"] == "warn"
    assert out["has_webhook"] is False
    assert out["webhook_source"] is None


def test_get_notification_config_reports_webhook_presence_never_the_url(
    db_session: Any, monkeypatch: Any
) -> None:
    """A webhook URL is a bearer credential — anyone holding it can post into the
    channel. It is stored as a secret reference, and this tool must report its
    presence only; neither the URL nor the reference may appear."""
    from backend.app.db.models import SuiteNotification

    user = _user(db_session)
    suite = _suite(db_session, user)
    db_session.add(
        SuiteNotification(
            suite_id=suite.id,
            enabled=True,
            alert_on="fail",
            webhook_secret_ref="notif-webhook-abc123",
            email_recipients="ops@acme.io",
        )
    )
    db_session.commit()
    _as(monkeypatch, db_session, user)

    out = server.get_notification_config(str(suite.id))
    assert out["configured"] is True
    assert out["has_webhook"] is True
    assert out["has_slack_webhook"] is False
    assert out["alert_on"] == "fail"
    assert out["email_recipients"] == "ops@acme.io"
    # Asserted on the VALUES against the stored reference, not on field names — a
    # name-shaped check would have to be revised every time a legitimate field
    # like `webhook_source` is added, and would still miss a leak under a
    # differently-named key.
    stored = db_session.query(SuiteNotification).filter_by(suite_id=suite.id).one()
    assert stored.webhook_secret_ref not in str(out)


def test_get_notification_config_denied_for_inaccessible_suite(
    db_session: Any, monkeypatch: Any
) -> None:
    owner = _user(db_session, "owner@acme.io")
    suite = _suite(db_session, owner)
    _as(monkeypatch, db_session, _user(db_session, "outsider@acme.io"))
    with pytest.raises(ToolError):
        server.get_notification_config(str(suite.id))


def test_list_connections_rejects_an_unknown_type_or_env(db_session: Any, monkeypatch: Any) -> None:
    """An unknown filter value must raise, not return `[]` — which reads as
    "nothing is connected" (#828), the exact shape `list_trigger_bindings` already
    guards for `provider`."""
    _as(monkeypatch, db_session, _user(db_session))
    with pytest.raises(ToolError):
        server.list_connections(type="databricks")
    with pytest.raises(ToolError):
        server.list_connections(env="staging")


def test_list_schedules_reports_the_precomputed_next_fire(
    db_session: Any, monkeypatch: Any
) -> None:
    """`next_run_at` is computed by the scheduler in the schedule's own timezone
    and is therefore already DST-correct — which re-deriving it from a cron string
    against an IANA zone downstream is not."""
    user = _user(db_session)
    suite = _suite(db_session, user)
    when = datetime.now(UTC) + timedelta(hours=6)
    _schedule(db_session, suite, user, next_run_at=when, timezone="America/Toronto")
    _as(monkeypatch, db_session, user)

    (sched,) = server.list_schedules()
    assert sched["next_run_at"] == when.isoformat()
    assert sched["timezone"] == "America/Toronto"


def test_list_trigger_bindings_workspace_admin_sees_unowned_bindings(
    db_session: Any, monkeypatch: Any, make_workspace_admin: Any
) -> None:
    """Parity with `list_schedules`, which got the workspace-admin view in the same
    commit — an admin seeing every schedule but zero bindings is not a defensible
    place to land (ADR 0027)."""
    owner = _user(db_session, "owner@acme.io")
    suite = _suite(db_session, owner)
    binding = _binding(db_session, suite, owner)
    admin = _user(db_session, "admin@acme.io")
    make_workspace_admin(admin.email)
    _as(monkeypatch, db_session, admin)

    assert str(binding.id) in {b["id"] for b in server.list_trigger_bindings()}


def test_list_trigger_bindings_accepts_every_orchestration_provider(
    db_session: Any, monkeypatch: Any
) -> None:
    """Driven off the shared vocabulary, so adding a provider can't leave a
    hardcoded literal behind — the way `dbt` (ADR 0029) was left behind."""
    from backend.app.db.models import ORCHESTRATION_PROVIDERS

    _as(monkeypatch, db_session, _user(db_session))
    for provider in ORCHESTRATION_PROVIDERS:
        assert server.list_trigger_bindings(provider=provider) == []


def test_get_adf_pipeline_status_accepts_dbt(db_session: Any, monkeypatch: Any) -> None:
    """The obvious next call after `list_trigger_bindings(provider="dbt")` returns
    a dbt binding. It used to raise on a provider DataQ has supported since
    ADR 0029."""
    _as(monkeypatch, db_session, _user(db_session))
    assert server.get_adf_pipeline_status(provider="dbt") == []


def test_get_notification_config_credits_the_workspace_channels(
    db_session: Any, monkeypatch: Any
) -> None:
    """The commonest deployment shape is one workspace webhook and no per-suite
    overrides. Reporting only the per-suite half answers "who gets told when
    orders fails?" with "nobody" for exactly that shape.
    """
    from backend.app.core.config import get_settings

    user = _user(db_session)
    suite = _suite(db_session, user)
    monkeypatch.setenv("TEAMS_WEBHOOK_SECRET_NAME", "workspace-teams-hook")
    monkeypatch.setenv("EMAIL_TO", "oncall@acme.io")
    # The SMTP transport too: recipients alone don't deliver anything.
    monkeypatch.setenv("EMAIL_USERNAME", "dataq@acme.io")
    monkeypatch.setenv("EMAIL_PASSWORD_SECRET_NAME", "smtp-password")
    monkeypatch.setenv("EMAIL_FROM", "dataq@acme.io")
    get_settings.cache_clear()
    _as(monkeypatch, db_session, user)
    try:
        out = server.get_notification_config(str(suite.id))
    finally:
        get_settings.cache_clear()

    assert out["configured"] is False
    assert out["has_webhook"] is True
    assert out["webhook_source"] == "workspace"
    assert out["has_email_recipients"] is True
    assert out["email_recipients_source"] == "workspace"
    # Slack is genuinely unset here, so it stays honestly absent.
    assert out["has_slack_webhook"] is False
    assert out["slack_webhook_source"] is None
    # And the workspace secret NAME is not part of the answer.
    assert "workspace-teams-hook" not in str(out)


def test_get_notification_config_prefers_the_suites_own_override(
    db_session: Any, monkeypatch: Any
) -> None:
    from backend.app.core.config import get_settings
    from backend.app.db.models import SuiteNotification

    user = _user(db_session)
    suite = _suite(db_session, user)
    db_session.add(
        SuiteNotification(suite_id=suite.id, webhook_secret_ref="suite-hook", alert_on="fail")
    )
    db_session.commit()
    monkeypatch.setenv("TEAMS_WEBHOOK_SECRET_NAME", "workspace-teams-hook")
    get_settings.cache_clear()
    _as(monkeypatch, db_session, user)
    try:
        out = server.get_notification_config(str(suite.id))
    finally:
        get_settings.cache_clear()

    assert out["has_webhook"] is True
    assert out["webhook_source"] == "suite"


# ────────── Tier-1 performance + export (#529) ────────────────────────────


def test_get_suite_performance_ranks_worst_first(db_session: Any, monkeypatch: Any) -> None:
    user = _user(db_session)
    healthy = _suite(db_session, user)
    broken = _suite(db_session, user)
    _run_with_results(db_session, healthy, outcomes=("pass", "pass"))
    _run_with_results(db_session, broken, outcomes=("critical", "critical"))
    _as(monkeypatch, db_session, user)

    out = server.get_suite_performance()
    assert out[0]["suite_id"] == str(broken.id)
    assert out[0]["state"] == "critical"
    assert out[-1]["suite_id"] == str(healthy.id)
    assert isinstance(out[0]["score"], float)


def test_get_suite_performance_omits_a_suite_with_no_countable_run(
    db_session: Any, monkeypatch: Any
) -> None:
    """Absence means "no health to report", never "healthy" — a suite that has
    never run, or whose latest run is still going, has no verdict to give. The
    docstring says so because an LLM would otherwise read a short list as a clean
    bill of health for everything missing from it."""
    user = _user(db_session)
    never_run = _suite(db_session, user)
    running = _suite(db_session, user)
    _run_with_results(db_session, running, status="running", outcomes=("pass",))
    _as(monkeypatch, db_session, user)

    listed = {p["suite_id"] for p in server.get_suite_performance()}
    assert str(never_run.id) not in listed
    assert str(running.id) not in listed


def test_get_suite_performance_hides_unowned_suites(db_session: Any, monkeypatch: Any) -> None:
    owner = _user(db_session, "owner@acme.io")
    suite = _suite(db_session, owner)
    _run_with_results(db_session, suite, outcomes=("fail",))
    _as(monkeypatch, db_session, _user(db_session, "outsider@acme.io"))
    assert server.get_suite_performance() == []


def test_get_suite_performance_advertises_no_window_argument() -> None:
    """`_suite_performance` scores each suite's LATEST run and takes no window, so
    a `window_days` argument was inert — identical rankings for 1 day and 90. A
    knob that does nothing is worse than no knob on an LLM-facing tool: it will be
    used, and then a difference that is not there will be explained."""
    import asyncio

    tool = asyncio.run(server.mcp.get_tool("get_suite_performance"))
    assert tool is not None
    assert tool.parameters.get("properties", {}) == {}


def test_export_suite_emits_definitions_in_stable_order(db_session: Any, monkeypatch: Any) -> None:
    user = _user(db_session)
    suite = _suite(db_session, user)
    _check(db_session, suite, name="first")
    _check(db_session, suite, name="second")
    _as(monkeypatch, db_session, user)

    doc = server.export_suite(str(suite.id))
    assert doc["name"] == "Orders"
    assert [c["name"] for c in doc["checks"]] == ["first", "second"]
    assert "version" in doc
    # Definitions only — no results, no run history.
    assert "results" not in doc and "runs" not in doc


def test_export_suite_coerces_decimal_thresholds_to_json_safe_numbers(
    db_session: Any, monkeypatch: Any
) -> None:
    """The #1273 class: thresholds are NUMERIC, so the service hands back
    `Decimal`. REST has Pydantic; MCP hands this dict to a JSON encoder, which
    raises on Decimal and takes the whole response down. Asserted by actually
    serializing, not by an isinstance check — the encoder is the thing that
    breaks, so it is the thing that must be exercised."""
    import json

    user = _user(db_session)
    suite = _suite(db_session, user)
    _check(db_session, suite, warn_threshold=Decimal("0.01"), fail_threshold=Decimal("0.05"))
    _as(monkeypatch, db_session, user)

    doc = server.export_suite(str(suite.id))
    assert doc["checks"][0]["warn_threshold"] == 0.01
    assert doc["checks"][0]["critical_threshold"] is None
    json.dumps(doc)  # would raise TypeError on a stray Decimal


def test_export_suite_denied_for_inaccessible_suite(db_session: Any, monkeypatch: Any) -> None:
    owner = _user(db_session, "owner@acme.io")
    suite = _suite(db_session, owner)
    _as(monkeypatch, db_session, _user(db_session, "outsider@acme.io"))
    with pytest.raises(ToolError):
        server.export_suite(str(suite.id))


def test_get_notification_config_does_not_claim_email_without_an_smtp_transport(
    db_session: Any, monkeypatch: Any
) -> None:
    """Recipients alone deliver nothing: `EmailPublisher.publish` no-ops unless the
    workspace SMTP username, password secret AND sender are all configured. A
    deployment that named recipients but never wired a mailer would otherwise be
    told email alerting is on."""
    from backend.app.core.config import get_settings

    user = _user(db_session)
    suite = _suite(db_session, user)
    monkeypatch.setenv("EMAIL_TO", "oncall@acme.io")
    monkeypatch.delenv("EMAIL_USERNAME", raising=False)
    monkeypatch.delenv("EMAIL_PASSWORD_SECRET_NAME", raising=False)
    get_settings.cache_clear()
    _as(monkeypatch, db_session, user)
    try:
        out = server.get_notification_config(str(suite.id))
    finally:
        get_settings.cache_clear()

    assert out["has_email_recipients"] is False
    assert out["email_recipients_source"] is None


def test_list_trigger_bindings_rejects_an_unknown_env(db_session: Any, monkeypatch: Any) -> None:
    """`env` gets the same guard as `provider`: a typo'd value returning `[]` reads
    as "nothing is wired up" (#828)."""
    _as(monkeypatch, db_session, _user(db_session))
    with pytest.raises(ToolError):
        server.list_trigger_bindings(env="staging")


# ──────────────── the coarse workspace-role axis on MCP (ADR 0033) ─────────


def test_require_role_ranks_the_same_way_the_rest_gate_does(db_session: Any) -> None:
    """`_require_role` is the MCP twin of `core.auth.require_role`, which is a
    FastAPI dependency MCP cannot use. The thing that must hold is that both
    resolve and rank through the SAME `core.roles` policy, so the two surfaces
    cannot disagree about who is an admin."""
    from backend.app.db.models import ADMIN_ROLE, DEFAULT_WORKSPACE_ROLE, VIEWER_ROLE

    viewer = User(aad_object_id=uuid.uuid4().hex, email="v@acme.io", role=VIEWER_ROLE)
    member = User(aad_object_id=uuid.uuid4().hex, email="m@acme.io", role=DEFAULT_WORKSPACE_ROLE)
    admin = User(aad_object_id=uuid.uuid4().hex, email="a@acme.io", role=ADMIN_ROLE)

    # A higher tier satisfies a lower requirement; the reverse is refused.
    server._require_role(admin, DEFAULT_WORKSPACE_ROLE)
    server._require_role(member, DEFAULT_WORKSPACE_ROLE)
    server._require_role(admin, ADMIN_ROLE)
    for user, minimum in [
        (viewer, DEFAULT_WORKSPACE_ROLE),
        (viewer, ADMIN_ROLE),
        (member, ADMIN_ROLE),
    ]:
        with pytest.raises(ToolError) as exc:
            server._require_role(user, minimum)
        # The denial names both sides, like the REST gate's `have`/`need` detail —
        # an LLM told only "denied" will retry rather than report why.
        assert minimum in str(exc.value) and user.role in str(exc.value)


def test_require_role_honours_the_admin_email_allowlist(
    db_session: Any, make_workspace_admin: Any
) -> None:
    """The allowlist is ADR 0033's bootstrap seed + break-glass, and `resolve_role`
    is where the two admin sources compose. Going through `resolve_role` rather
    than reading `user.role` is what keeps that true here too."""
    from backend.app.db.models import ADMIN_ROLE, DEFAULT_WORKSPACE_ROLE

    user = User(aad_object_id=uuid.uuid4().hex, email="boot@acme.io", role=DEFAULT_WORKSPACE_ROLE)
    with pytest.raises(ToolError):
        server._require_role(user, ADMIN_ROLE)

    make_workspace_admin(user.email)
    server._require_role(user, ADMIN_ROLE)


# ───────────────────── Tier-2 check mutations (#529) ──────────────────────


def test_update_check_leaves_omitted_fields_alone(db_session: Any, monkeypatch: Any) -> None:
    """PATCH semantics: omission means "unchanged", not "clear". The tool docstring
    tells an LLM this outright, because the natural repair for a field it wants
    gone — passing 0 or "" — would SET that value, not remove it."""
    user = _user(db_session)
    suite = _suite(db_session, user)
    check = _check(db_session, suite, dimension="completeness", warn_threshold=Decimal("0.01"))
    _as(monkeypatch, db_session, user)

    out = server.update_check(str(suite.id), str(check.id), name="renamed")
    assert out["name"] == "renamed"
    assert out["warn_threshold"] == 0.01
    assert out["dimension"] == "completeness"
    assert out["config"] == {"column": "EMAIL"}


def test_update_check_converts_thresholds_without_binary_float_drift(
    db_session: Any, monkeypatch: Any
) -> None:
    """`Decimal(0.05)` binds the binary float's full expansion, so the stored
    threshold would not be the number asked for. `_dec` goes via `str`."""
    user = _user(db_session)
    suite = _suite(db_session, user)
    check = _check(db_session, suite)
    _as(monkeypatch, db_session, user)

    server.update_check(str(suite.id), str(check.id), fail_threshold=0.05)
    db_session.refresh(check)
    assert check.fail_threshold == Decimal("0.05")


def test_update_check_records_a_new_version(db_session: Any, monkeypatch: Any) -> None:
    """Every update snapshots the post-update state (#280), so a change made by an
    LLM is as reviewable and reversible as one made in the app."""
    user = _user(db_session)
    suite = _suite(db_session, user)
    check = _check(db_session, suite)
    _as(monkeypatch, db_session, user)

    before = len(check_service.list_check_versions(db_session, suite.id, check.id))
    server.update_check(str(suite.id), str(check.id), name="renamed")
    after = check_service.list_check_versions(db_session, suite.id, check.id)
    assert len(after) == before + 1
    assert after[0].changed_by == user.id


def test_update_check_cross_suite_is_refused(db_session: Any, monkeypatch: Any) -> None:
    user = _user(db_session)
    mine = _suite(db_session, user)
    foreign = _check(db_session, _suite(db_session, _user(db_session, "o@acme.io")))
    _as(monkeypatch, db_session, user)
    with pytest.raises(ToolError):
        server.update_check(str(mine.id), str(foreign.id), name="x")


def test_delete_check_removes_it_and_names_what_went(db_session: Any, monkeypatch: Any) -> None:
    """The name is read BEFORE the delete: an LLM confirming "removed the row-count
    check" cannot look it up afterwards, and an id alone is not a confirmation a
    user can check."""
    user = _user(db_session)
    suite = _suite(db_session, user)
    check = _check(db_session, suite, name="row count")
    _as(monkeypatch, db_session, user)

    out = server.delete_check(str(suite.id), str(check.id))
    assert out == {"deleted": True, "check_id": str(check.id), "name": "row count"}
    assert db_session.get(Check, check.id) is None


def test_delete_check_also_destroys_its_result_history(db_session: Any, monkeypatch: Any) -> None:
    """`results.check_id` is `ondelete="CASCADE"`, so a delete takes the history
    with it.

    This test was written to prove the opposite — the docstring first claimed past
    results survive — and failing it is what sent me to the FK. Pinned here
    because the tool's docstring is the only warning an LLM gets before doing
    something irreversible, and a docstring that under-describes the blast radius
    is worse than no docstring at all.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    check = _check(db_session, suite)
    run = Run(suite_id=suite.id, status="succeeded")
    db_session.add(run)
    db_session.flush()
    db_session.add(Result(run_id=run.id, check_id=check.id, status="pass"))
    db_session.commit()
    _as(monkeypatch, db_session, user)

    server.delete_check(str(suite.id), str(check.id))
    assert run_service.list_results(db_session, run.id) == []


def test_snooze_check_sets_and_clears_one_piece_of_state(db_session: Any, monkeypatch: Any) -> None:
    """One tool, not a snooze/unsnooze pair: it is a single field with two values,
    and two names for it would be a selection problem for no gain."""
    user = _user(db_session)
    suite = _suite(db_session, user)
    check = _check(db_session, suite)
    _as(monkeypatch, db_session, user)

    snoozed = server.snooze_check(str(suite.id), str(check.id), hours=4)
    assert snoozed["snoozed"] is True
    assert snoozed["alert_snoozed_until"] is not None

    cleared = server.snooze_check(str(suite.id), str(check.id))
    assert cleared["snoozed"] is False
    assert cleared["alert_snoozed_until"] is None


def test_snooze_check_bounds_hours_in_the_tool_schema() -> None:
    """An LLM-supplied duration arrives unbounded — a negative would set a snooze
    in the PAST (silently no-op, reported as success), and an absurd one would
    mute a check for centuries."""
    import asyncio

    tool = asyncio.run(server.mcp.get_tool("snooze_check"))
    assert tool is not None
    schema = tool.parameters["properties"]["hours"]
    text = str(schema)
    assert "exclusiveMinimum" in text or "minimum" in text
    assert "8760" in text


def test_dryrun_check_persists_nothing(db_session: Any, monkeypatch: Any) -> None:
    """The whole point of the authoring loop: preview, adjust, preview — with no
    check row, no run and no result left behind."""
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)
    monkeypatch.setattr(
        dryrun_service,
        "dry_run_check",
        lambda *a, **k: SimpleNamespace(
            status="pass",
            metric_value=Decimal("0.0"),
            observed_value={"unexpected_count": 0},
            expected_value={"max": 0},
        ),
    )

    out = server.dryrun_check(
        str(suite.id),
        expectation_type="expect_column_values_to_not_be_null",
        config={"column": "EMAIL"},
    )
    assert out["status"] == "pass"
    assert out["metric_value"] == 0.0
    assert db_session.scalars(select(Check).where(Check.suite_id == suite.id)).all() == []
    assert db_session.scalars(select(Run).where(Run.suite_id == suite.id)).all() == []


def test_dryrun_check_redacts_observed_values_like_the_results_tools(
    db_session: Any, monkeypatch: Any
) -> None:
    """The REST dry-run route does NOT redact (#1419) — defensible there, where the
    consumer is the author's own editor panel. Here the consumer is a model that
    will quote the value onward, and `get_suite_results` would mask this very
    column on this very suite: an LLM seeing a value in one and a mask in the
    other has no way to tell which is the truth.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    suite.column_policy = {"pii_columns": ["EMAIL"]}
    db_session.commit()
    _as(monkeypatch, db_session, user)

    def _fake(*_a: Any, **kwargs: Any) -> Any:
        column = kwargs["config"]["column"]
        values = ["ada@acme.io"] if column == "EMAIL" else ["SHIPPED", "PENDING"]
        return SimpleNamespace(
            status="fail",
            metric_value=Decimal("3"),
            observed_value={"observed_value": values},
            expected_value=None,
        )

    monkeypatch.setattr(dryrun_service, "dry_run_check", _fake)

    masked = server.dryrun_check(
        str(suite.id),
        expectation_type="expect_column_values_to_be_in_set",
        config={"column": "EMAIL"},
    )
    assert "ada@acme.io" not in str(masked["observed_value"])

    # And a non-sensitive column still shows its values — a redactor that masks
    # everything is not a redactor, it is a blindfold, and the preview would be
    # useless for the authoring loop it exists to serve.
    shown = server.dryrun_check(
        str(suite.id),
        expectation_type="expect_column_values_to_be_in_set",
        config={"column": "STATUS"},
    )
    assert "SHIPPED" in str(shown["observed_value"])


def test_update_check_config_replaces_wholesale_which_the_docstring_warns_about(
    db_session: Any, monkeypatch: Any
) -> None:
    """`config` is assigned, not merged — the one argument where "omitted means
    unchanged" does NOT extend to the keys inside it.

    Pinned because the failure is silent and plausible: sending only the key you
    meant to change drops the rest, the result is still a valid check, so it saves
    and reports success. This is REST's own PATCH semantics, so the fix is the
    docstring telling an LLM to read-modify-write rather than a divergence here.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    check = _check(
        db_session,
        suite,
        expectation_type="expect_column_values_to_be_between",
        config={"column": "AMT", "min_value": 0, "max_value": 10},
    )
    _as(monkeypatch, db_session, user)

    out = server.update_check(
        str(suite.id), str(check.id), config={"column": "AMT", "max_value": 100}
    )
    assert out["config"] == {"column": "AMT", "max_value": 100}
    assert "min_value" not in out["config"]

    # The documented safe path — read, edit, write back — keeps everything.
    current = dict(server.get_check(str(suite.id), str(check.id))["config"])
    current["max_value"] = 200
    restored = server.update_check(str(suite.id), str(check.id), config=current)
    assert restored["config"] == {"column": "AMT", "max_value": 200}
