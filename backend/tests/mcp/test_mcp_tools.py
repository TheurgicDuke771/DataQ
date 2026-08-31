"""DB-backed tests for the MCP tools (real Postgres)."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from fastmcp.exceptions import ToolError
from sqlalchemy import select

from backend.app.db.models import (
    Asset,
    Check,
    Connection,
    Incident,
    PipelineRun,
    Result,
    Run,
    Share,
    Suite,
    User,
)
from backend.app.mcp import server
from backend.app.services import (
    asset_view_service,
    check_service,
    connection_service,
    dryrun_service,
    orchestration_service,
    profile_service,
    run_dispatch,
    run_service,
    schedule_service,
    suite_service,
    trigger_binding_service,
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
    """`list_suites` must issue a FIXED number of queries, not O(suites) (#947)."""
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
    # 4 suites, and the whole tool stays in single digits.
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


def test_get_pipeline_status_correlates_dq_run(db_session: Any, monkeypatch: Any) -> None:
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

    out = server.get_pipeline_status()["pipeline_runs"]
    assert out[0]["pipeline"] == "load_orders"
    assert out[0]["dq_run"]["status"] == "succeeded"


def _adf_run_on_unowned_suite(db_session: Any) -> User:
    """Seed a pipeline run correlated to a DQ run on a suite owned by someone
    else, and return a fresh outsider to view it. Shared by the admin +
    non-admin correlation-visibility tests below.
    """
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


def test_get_pipeline_status_hides_unowned_correlation_from_non_admin(
    db_session: Any, monkeypatch: Any
) -> None:
    # The pipeline run itself is workspace-wide, but the correlated DQ run is
    # scoped: a non-admin outsider sees the pipeline row with dq_run == None.
    outsider = _adf_run_on_unowned_suite(db_session)
    _as(monkeypatch, db_session, outsider)
    out = server.get_pipeline_status()["pipeline_runs"]
    assert out[0]["pipeline"] == "load_orders"
    assert out[0]["dq_run"] is None


def test_get_pipeline_status_workspace_admin_correlates_unowned_run(
    db_session: Any, monkeypatch: Any, make_workspace_admin: Any
) -> None:
    # A workspace-admin sees the correlated DQ run even on a suite they don't own
    # (ADR 0027 parity with the REST orchestration view).
    admin = _adf_run_on_unowned_suite(db_session)
    make_workspace_admin(admin.email)
    _as(monkeypatch, db_session, admin)
    out = server.get_pipeline_status()["pipeline_runs"]
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
    or a nested config value.
    """
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
    Postgres `StringDataRightTruncation` on the INSERT.
    """
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


def test_create_check_refuses_an_unallowlisted_type_and_names_the_alternatives(
    db_session: Any, monkeypatch: Any
) -> None:
    """#1510 over MCP. `_service_errors` keeps only the message, so the accepted types have to
    travel IN it — otherwise the model is told "no" with no way to find the yes, and guesses again.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)

    with pytest.raises(ToolError) as exc_info:
        server.create_check(
            str(suite.id),
            name="amount ceiling",
            expectation_type="expect_column_max_to_be_between",
            config={"column": "amount", "min_value": 1, "max_value": 100},
        )
    message = str(exc_info.value)
    assert "not in DataQ's vetted set" in message
    assert "Accepted values:" in message
    assert "expect_column_values_to_not_be_null" in message
    assert db_session.query(Check).filter_by(suite_id=suite.id).count() == 0


def test_service_error_text_appends_accepted_values_only_when_present() -> None:
    """The other half: an error with nothing to recover from must not grow a stray suffix."""
    from backend.app.core.errors import DataQError

    plain = DataQError("check not found", detail={"check_id": "abc"})
    assert server._tool_error_text(plain) == "check not found"

    listed = DataQError("check kind 'x' is not supported in v1", detail={"supported": ["a", "b"]})
    assert server._tool_error_text(listed).endswith("Accepted values: a, b")

    long = DataQError("nope", detail={"supported": [f"t{i}" for i in range(70)]})
    assert long.detail is not None
    text = server._tool_error_text(long)
    assert text.endswith("(+10)")
    assert "t59" in text and "t60" not in text


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
    """#327 review, P4: `top_n` is no longer only a result-size knob."""
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
    it again would double-fold (#721 code review).
    """
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
    its own fold (#721 code review).
    """
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
    what to set instead of a bare validation failure.
    """
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
    resolved concrete file.
    """
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
    passes those hosts so the guard can't shadow the real auth gate.
    """
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
    without coupling the test to that private helper.
    """
    from fnmatch import fnmatchcase

    def matches(host: str, patterns: list[str]) -> bool:
        return any(p == "*" or fnmatchcase(host, p) for p in patterns)

    hosts = ["*.azurecontainerapps.io", "api", "localhost", "127.0.0.1"]
    # The SHAPE of the upstream Host nginx forwards (DATAQ_API_UPSTREAM, internal ingress):
    # `<app>.internal.<env-hash>.<region>.azurecontainerapps.io`.
    assert matches("dataq-app-api.internal.example-0a1b2c3d.westus2.azurecontainerapps.io", hosts)
    assert matches("api", hosts)  # docker-compose
    assert not matches("evil.example.com", hosts)  # still rejects the rest


def test_allowed_hosts_come_from_settings_not_hardcoded(monkeypatch: Any) -> None:
    """A non-ACA deployment can configure the allowlist (#728)."""
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
    """Empty config keeps the deployed behaviour untouched (#728)."""
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
    overclaim class, reintroduced for every MCP consumer at once.
    """
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
    branch on presence without a backfill.
    """
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
    An LLM asked "why was there no alert?" can only answer if it can see this.
    """
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
    suite they can (the 404-no-leak discipline, ADR 0027).
    """
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
    takes newest-first in SQL and reverses).
    """
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
    directly from Python bypasses FastMCP's validation entirely.
    """
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
    so does this.
    """
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
    against.
    """
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
    with total confidence. `total` + `truncated` make the cut observable.
    """
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
    Merging the two is the misreport this shape exists to prevent.
    """
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
    report 0 total — not a misleading `0/N` that reads as total failure.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _run_with_results(db_session, suite, outcomes=("skip", "error"))
    _as(monkeypatch, db_session, user)

    (run,) = server.list_runs()["runs"]
    assert run["checks_total"] == 0
    assert run["worst_severity"] is None


def test_list_runs_total_is_independent_of_the_page(db_session: Any, monkeypatch: Any) -> None:
    """A short page must not be mistaken for the end of the list (#1108): `total`
    counts the whole matching population, not the slice.
    """
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
    denial must not confirm the suite exists either.
    """
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
    "no runs in that state" (#828).
    """
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
    history that `get_suite_results` has already moved past.
    """
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
    genuine fraction of the suite — which an LLM would summarise as the answer.
    """
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
    """The aggregate overclaims exactly the way the row list does (#318)."""
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


def test_list_runs_since_hours_excludes_older_runs(db_session: Any, monkeypatch: Any) -> None:
    """#1442: 'what ran today' needs an actual time bound, not just the newest N."""
    user = _user(db_session)
    suite = _suite(db_session, user)
    now = datetime.now(UTC)
    old = Run(suite_id=suite.id, status="succeeded", created_at=now - timedelta(hours=48))
    recent = Run(suite_id=suite.id, status="succeeded", created_at=now - timedelta(hours=2))
    db_session.add_all([old, recent])
    db_session.commit()
    _as(monkeypatch, db_session, user)

    out = server.list_runs(since_hours=24)
    assert out["total"] == 1
    assert {r["id"] for r in out["runs"]} == {str(recent.id)}


def test_list_runs_since_and_until_hours_bound_a_window(db_session: Any, monkeypatch: Any) -> None:
    """A bounded window (`since_hours=48, until_hours=24` = 'yesterday') must
    exclude runs both newer than `until_hours` and older than `since_hours`.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    now = datetime.now(UTC)
    too_old = Run(suite_id=suite.id, status="succeeded", created_at=now - timedelta(hours=60))
    yesterday = Run(suite_id=suite.id, status="succeeded", created_at=now - timedelta(hours=36))
    too_recent = Run(suite_id=suite.id, status="succeeded", created_at=now - timedelta(hours=2))
    db_session.add_all([too_old, yesterday, too_recent])
    db_session.commit()
    _as(monkeypatch, db_session, user)

    out = server.list_runs(since_hours=48, until_hours=24)
    assert out["total"] == 1
    assert {r["id"] for r in out["runs"]} == {str(yesterday.id)}


def test_list_runs_rejects_until_hours_not_less_than_since_hours(
    db_session: Any, monkeypatch: Any
) -> None:
    user = _user(db_session)
    _suite(db_session, user)
    _as(monkeypatch, db_session, user)

    with pytest.raises(ToolError):
        server.list_runs(since_hours=24, until_hours=24)


# ─────────────── Tier-1 ops/config reads (#529) ───────────────────────────


def test_list_connections_returns_metadata_and_health_but_never_config(
    db_session: Any, monkeypatch: Any
) -> None:
    """The #529 constraint, asserted as an absence rather than assumed."""
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)

    (conn,) = [c for c in server.list_connections() if c["id"] == str(suite.connection_id)]
    assert conn["type"] == "snowflake"
    assert conn["env"] == "dev"
    assert conn["has_secret"] is True
    assert "config" not in conn
    assert "secret_ref" not in conn
    # And nothing FROM the config leaked under another key — asserted against the stored config's
    # own values.
    stored = db_session.get(Connection, suite.connection_id)
    leaked = set(stored.config.values()) & {v for v in conn.values() if isinstance(v, str)}
    assert not leaked, leaked


def test_list_connections_reports_unknown_health_as_null_not_healthy(
    db_session: Any, monkeypatch: Any
) -> None:
    """A connection with no runs is *unknown*, and the docstring says so. Reading
    a null as reassurance is the #828 blindness in a new place.
    """
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
    it shows on the run, not the connection.
    """
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
    assert sched["next_run_at"]


def test_list_schedules_includes_disabled_ones_and_says_so(
    db_session: Any, monkeypatch: Any
) -> None:
    """A disabled schedule still reads back — the `enabled` flag is the answer, not
    the row's presence. Filtering must be explicit.
    """
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
    rather than return an empty list that reads as "nothing is wired up".
    """
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
    tool is actually asked.
    """
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
    presence only; neither the URL nor the reference may appear.
    """
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
    # Asserted on the VALUES against the stored reference, not on field names — a name-shaped check
    # would have to be revised every time a legitimate field like `webhook_source` is added.
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
    guards for `provider`.
    """
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
    against an IANA zone downstream is not.
    """
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
    place to land (ADR 0027).
    """
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
    hardcoded literal behind — the way `dbt` (ADR 0029) was left behind.
    """
    from backend.app.db.models import ORCHESTRATION_PROVIDERS

    _as(monkeypatch, db_session, _user(db_session))
    for provider in ORCHESTRATION_PROVIDERS:
        assert server.list_trigger_bindings(provider=provider) == []


def test_get_pipeline_status_accepts_dbt(db_session: Any, monkeypatch: Any) -> None:
    """The obvious next call after `list_trigger_bindings(provider="dbt")` returns
    a dbt binding. It used to raise on a provider DataQ has supported since
    ADR 0029.
    """
    _as(monkeypatch, db_session, _user(db_session))
    assert server.get_pipeline_status(provider="dbt")["pipeline_runs"] == []


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
    bill of health for everything missing from it.
    """
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
    used, and then a difference that is not there will be explained.
    """
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
    """The #1273 class: thresholds are NUMERIC, so the service hands back `Decimal`. REST has
    Pydantic; MCP hands this dict to a JSON encoder, which raises on Decimal and takes the whole
    response down. Asserted by actually serializing, not by an isinstance check — the encoder is
    the thing that breaks, so it is the thing that must be exercised.
    """
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
    told email alerting is on.
    """
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
    as "nothing is wired up" (#828).
    """
    _as(monkeypatch, db_session, _user(db_session))
    with pytest.raises(ToolError):
        server.list_trigger_bindings(env="staging")


# ──────────────── the coarse workspace-role axis on MCP (ADR 0033) ─────────


def test_require_role_ranks_the_same_way_the_rest_gate_does(db_session: Any) -> None:
    """`_require_role` is the MCP twin of `core.auth.require_role`, which is a
    FastAPI dependency MCP cannot use. The thing that must hold is that both
    resolve and rank through the SAME `core.roles` policy, so the two surfaces
    cannot disagree about who is an admin.
    """
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
    than reading `user.role` is what keeps that true here too.
    """
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
    gone — passing 0 or "" — would SET that value, not remove it.
    """
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
    threshold would not be the number asked for. `_dec` goes via `str`.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    check = _check(db_session, suite)
    _as(monkeypatch, db_session, user)

    server.update_check(str(suite.id), str(check.id), fail_threshold=0.05)
    db_session.refresh(check)
    assert check.fail_threshold == Decimal("0.05")


def test_update_check_records_a_new_version(db_session: Any, monkeypatch: Any) -> None:
    """Every update snapshots the post-update state (#280), so a change made by an
    LLM is as reviewable and reversible as one made in the app.
    """
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
    user can check.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    check = _check(db_session, suite, name="row count")
    _as(monkeypatch, db_session, user)

    out = server.delete_check(str(suite.id), str(check.id))
    assert out == {"deleted": True, "check_id": str(check.id), "name": "row count"}
    assert db_session.get(Check, check.id) is None


def test_delete_check_also_destroys_its_result_history(db_session: Any, monkeypatch: Any) -> None:
    """`results.check_id` is `ondelete="CASCADE"`, so a delete takes the history with it."""
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
    and two names for it would be a selection problem for no gain.
    """
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
    mute a check for centuries.
    """
    import asyncio

    tool = asyncio.run(server.mcp.get_tool("snooze_check"))
    assert tool is not None
    schema = tool.parameters["properties"]["hours"]
    text = str(schema)
    assert "exclusiveMinimum" in text or "minimum" in text
    assert "8760" in text


def test_dryrun_check_persists_nothing(db_session: Any, monkeypatch: Any) -> None:
    """The whole point of the authoring loop: preview, adjust, preview — with no
    check row, no run and no result left behind.
    """
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


def test_dryrun_check_refuses_a_monitor_kind_it_cannot_preview(
    db_session: Any, monkeypatch: Any
) -> None:
    """#1592: the docstring says `freshness`/`volume` have no dry-run at all —
    prove it against the REAL `dry_run_service.dry_run_check`, not a mock that
    would happily preview anything it's told to.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)

    with pytest.raises(ToolError):
        server.dryrun_check(
            str(suite.id),
            expectation_type="monitor:volume",
            kind="volume",
            config={"min_rows": 1},
        )


def test_dryrun_check_redacts_observed_values_like_the_results_tools(
    db_session: Any, monkeypatch: Any
) -> None:
    """The REST dry-run route does NOT redact (#1419) — defensible there, where the consumer is the
    author's own editor panel. Here the consumer is a model that will quote the value onward,
    and `get_suite_results` would mask this very column on this very suite: an LLM seeing a
    value in one and a mask in the other has no way to tell which is the truth.
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

    # And a non-sensitive column still shows its values — a redactor that masks everything is not a
    # redactor, it is a blindfold.
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


# ──────────── Tier-2 run / schedule / trigger mutations (#529) ─────────────


def test_cancel_run_marks_it_cancelled_and_revokes_the_task(
    db_session: Any, monkeypatch: Any
) -> None:
    """Both halves. The DB flip alone leaves a queued Celery task to run anyway —
    the row would say cancelled while the work happened, which is the worst of
    both answers.
    """
    revoked: list[str | None] = []
    user = _user(db_session)
    suite = _suite(db_session, user)
    run = Run(suite_id=suite.id, status="queued", celery_task_id="task-1")
    db_session.add(run)
    db_session.commit()
    _as(monkeypatch, db_session, user)
    monkeypatch.setattr(run_dispatch, "revoke_run", lambda tid: revoked.append(tid))

    out = server.cancel_run(str(run.id))
    assert out["status"] == "cancelled"
    assert revoked == ["task-1"]


def test_cancel_run_refuses_an_already_finished_run(db_session: Any, monkeypatch: Any) -> None:
    """It reports the real state rather than pretending it cancelled something —
    an LLM told "cancelled" about a run that already succeeded will say so.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    run = Run(suite_id=suite.id, status="succeeded")
    db_session.add(run)
    db_session.commit()
    _as(monkeypatch, db_session, user)

    with pytest.raises(ToolError) as exc:
        server.cancel_run(str(run.id))
    assert "already finished" in str(exc.value)
    assert "succeeded" in str(exc.value)


def test_cancel_run_denied_for_a_run_on_an_inaccessible_suite(
    db_session: Any, monkeypatch: Any
) -> None:
    owner = _user(db_session, "owner@acme.io")
    suite = _suite(db_session, owner)
    run = Run(suite_id=suite.id, status="queued")
    db_session.add(run)
    db_session.commit()
    _as(monkeypatch, db_session, _user(db_session, "outsider@acme.io"))
    with pytest.raises(ToolError):
        server.cancel_run(str(run.id))


def test_create_schedule_returns_the_resolved_next_fire(db_session: Any, monkeypatch: Any) -> None:
    """Returning `next_run_at` is the point: it lets the assistant confirm the
    INTERPRETATION back to the user, instead of restating the cron string they
    just supplied — which confirms nothing.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)

    out = server.create_schedule(str(suite.id), cron="0 2 * * *", timezone="America/Toronto")
    assert out["cron"] == "0 2 * * *"
    assert out["timezone"] == "America/Toronto"
    assert out["enabled"] is True
    assert out["next_run_at"]


def test_create_schedule_rejects_a_bad_cron_or_timezone(db_session: Any, monkeypatch: Any) -> None:
    """An invalid expression must be an error, not a schedule that quietly never
    fires — the failure mode a user would not discover until the run they were
    counting on did not happen.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)

    with pytest.raises(ToolError):
        server.create_schedule(str(suite.id), cron="not a cron")
    with pytest.raises(ToolError):
        server.create_schedule(str(suite.id), cron="0 2 * * *", timezone="Mars/Olympus")
    assert schedule_service.list_schedules(db_session, user_id=user.id) == []


def test_delete_schedule_removes_only_the_schedule(db_session: Any, monkeypatch: Any) -> None:
    """The suite and its checks survive — the docstring says only the automatic
    trigger goes, so that is what is checked.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _check(db_session, suite)
    _as(monkeypatch, db_session, user)
    created = server.create_schedule(str(suite.id), cron="0 2 * * *")

    out = server.delete_schedule(created["id"])
    assert out["deleted"] is True
    assert out["schedule_id"] == created["id"]
    # The response says WHAT was deleted, so a model handed the wrong id cannot
    # confirm the removal of something it never touched.
    assert out["cron"] == "0 2 * * *"
    assert out["suite_id"] == str(suite.id)
    assert schedule_service.list_schedules(db_session, user_id=user.id) == []
    assert db_session.get(Suite, suite.id) is not None
    assert len(check_service.list_checks(db_session, suite.id)) == 1


def test_delete_schedule_denied_on_an_inaccessible_suite(db_session: Any, monkeypatch: Any) -> None:
    """A schedule id alone must not be a way around suite scoping."""
    owner = _user(db_session, "owner@acme.io")
    suite = _suite(db_session, owner)
    _as(monkeypatch, db_session, owner)
    created = server.create_schedule(str(suite.id), cron="0 2 * * *")

    _as(monkeypatch, db_session, _user(db_session, "outsider@acme.io"))
    with pytest.raises(ToolError):
        server.delete_schedule(created["id"])


def test_create_trigger_binding_shapes_the_binding(db_session: Any, monkeypatch: Any) -> None:
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)

    out = server.create_trigger_binding(
        provider="adf", pipeline_or_dag_id="pl_nightly", env="dev", suite_id=str(suite.id)
    )
    assert out["provider"] == "adf"
    assert out["pipeline_or_dag_id"] == "pl_nightly"
    assert out["suite_id"] == str(suite.id)
    assert out["enabled"] is True
    assert out["warnings"] == []


def test_create_trigger_binding_surfaces_the_ambiguous_env_warning(
    db_session: Any, monkeypatch: Any
) -> None:
    """The #1186 advisory names a binding that will silently never fire. Dropping
    it here — it is only a warning, after all — would recreate the exact live
    incident it was built to surface, with an LLM cheerfully reporting success.
    """
    from backend.app.db.models import Connection

    user = _user(db_session)
    suite = _suite(db_session, user)
    for env in ("dev", "qa"):
        db_session.add(
            Connection(
                name=f"adf-{env}",
                type="adf",
                env=env,
                config={"factory_name": "shared-factory"},
                created_by=user.id,
            )
        )
    db_session.commit()
    _as(monkeypatch, db_session, user)

    out = server.create_trigger_binding(
        provider="adf", pipeline_or_dag_id="pl_nightly", env="dev", suite_id=str(suite.id)
    )
    assert out["warnings"], "the ambiguous-env advisory was dropped"
    assert out["warnings"][0]["code"] == "ambiguous_orchestration_url"
    assert "qa" in out["warnings"][0]["other_envs"]


def test_create_trigger_binding_rejects_an_unknown_provider_or_env(
    db_session: Any, monkeypatch: Any
) -> None:
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)

    with pytest.raises(ToolError):
        server.create_trigger_binding(
            provider="databricks", pipeline_or_dag_id="p", env="dev", suite_id=str(suite.id)
        )
    with pytest.raises(ToolError):
        server.create_trigger_binding(
            provider="adf", pipeline_or_dag_id="p", env="staging", suite_id=str(suite.id)
        )


def test_a_disabled_schedule_reports_no_next_fire(db_session: Any, monkeypatch: Any) -> None:
    """The column always holds a computed timestamp, but the dispatcher filters on
    `enabled` and never reaches it — so reporting the raw value names a fire time
    that will not happen. Both the create tool and the list tool have to agree,
    since an assistant may see either.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)

    created = server.create_schedule(str(suite.id), cron="0 2 * * *", enabled=False)
    assert created["enabled"] is False
    assert created["next_run_at"] is None

    (listed,) = server.list_schedules()
    assert listed["enabled"] is False
    assert listed["next_run_at"] is None
    # The stored expression is untouched — it starts firing when re-enabled.
    assert listed["cron"] == "0 2 * * *"


def test_trigger_binding_and_schedule_free_text_is_bounded_in_the_schema() -> None:
    """These land in `varchar` NOT NULL columns. An unbounded LLM-generated value
    reaches Postgres and raises a psycopg error — NOT a `DataQError`, so it
    escapes `_service_errors` and surfaces as an opaque internal failure rather
    than an actionable one (#567's class, in new columns).
    """
    import asyncio

    binding = asyncio.run(server.mcp.get_tool("create_trigger_binding"))
    assert binding is not None
    pipeline = binding.parameters["properties"]["pipeline_or_dag_id"]
    assert pipeline["minLength"] == 1 and pipeline["maxLength"] == 256

    schedule = asyncio.run(server.mcp.get_tool("create_schedule"))
    assert schedule is not None
    assert schedule.parameters["properties"]["cron"]["maxLength"] == 128
    assert schedule.parameters["properties"]["timezone"]["maxLength"] == 64


def test_create_trigger_binding_rejects_nul_bytes(db_session: Any, monkeypatch: Any) -> None:
    """Postgres cannot store NUL in text, and the driver's ValueError is not a
    `DataQError` — so without this the tool dies on an opaque internal error
    instead of an actionable one.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)

    with pytest.raises(ToolError) as exc:
        server.create_trigger_binding(
            provider="adf",
            pipeline_or_dag_id="pl\x00nightly",
            env="dev",
            suite_id=str(suite.id),
        )
    assert "NUL" in str(exc.value)


# ─────────── Tier-2 workspace-role-gated capabilities (#529) ──────────────


def test_test_connection_requires_the_member_role(db_session: Any, monkeypatch: Any) -> None:
    """A connection has no suite, so `require_permission` has nothing to gate on —
    this is the first tool where the coarse ADR-0033 axis is load-bearing rather
    than implied by the suite ladder.
    """
    from backend.app.db.models import DEFAULT_WORKSPACE_ROLE, VIEWER_ROLE

    viewer = _user(db_session, "viewer@acme.io")
    viewer.role = VIEWER_ROLE
    suite = _suite(db_session, viewer)
    db_session.commit()
    _as(monkeypatch, db_session, viewer)
    monkeypatch.setattr(connection_service, "test_connection", lambda *a, **k: None)

    with pytest.raises(ToolError) as exc:
        server.test_connection(str(suite.connection_id))
    assert "member" in str(exc.value)

    viewer.role = DEFAULT_WORKSPACE_ROLE
    db_session.commit()
    out = server.test_connection(str(suite.connection_id))
    assert out["ok"] is True
    assert out["type"] == "snowflake"


def test_test_connection_never_returns_a_credential(db_session: Any, monkeypatch: Any) -> None:
    """It reports whether the probe worked and nothing else — no secret, and not
    even the secret's reference.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)
    monkeypatch.setattr(connection_service, "test_connection", lambda *a, **k: None)

    out = server.test_connection(str(suite.connection_id))
    stored = db_session.get(Connection, suite.connection_id)
    assert stored.secret_ref not in str(out)
    assert "config" not in out and "secret_ref" not in out


def test_test_connection_surfaces_a_failure_as_an_actionable_error(
    db_session: Any, monkeypatch: Any
) -> None:
    """A dead credential is the reason to call this at all — the failure has to
    arrive as a clean message, not an opaque tool crash.
    """
    from backend.app.services.connection_service import ConnectionTestFailedError

    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)

    def _boom(*_a: Any, **_k: Any) -> None:
        raise ConnectionTestFailedError("authentication failed")

    monkeypatch.setattr(connection_service, "test_connection", _boom)
    with pytest.raises(ToolError) as exc:
        server.test_connection(str(suite.connection_id))
    assert "authentication failed" in str(exc.value)


def test_import_suite_requires_the_member_role(db_session: Any, monkeypatch: Any) -> None:
    """Creating a suite is Member+ (ADR 0033), and this is the SECOND door into
    suite creation — the class of gap #741's review found on `_probe`, where a
    sibling endpoint created the same resource under another name and was
    invisible to gating that lived on the obvious route.
    """
    from backend.app.db.models import DEFAULT_WORKSPACE_ROLE, VIEWER_ROLE

    viewer = _user(db_session, "viewer@acme.io")
    viewer.role = VIEWER_ROLE
    suite = _suite(db_session, viewer)
    db_session.commit()
    _as(monkeypatch, db_session, viewer)

    with pytest.raises(ToolError) as exc:
        server.import_suite(str(suite.connection_id), name="copied", checks=[])
    assert "member" in str(exc.value)
    # And nothing was created by the refused call.
    assert len(suite_service.list_suites(db_session, user_id=viewer.id)) == 1

    viewer.role = DEFAULT_WORKSPACE_ROLE
    db_session.commit()
    out = server.import_suite(str(suite.connection_id), name="copied", checks=[])
    assert out["name"] == "copied"
    assert len(suite_service.list_suites(db_session, user_id=viewer.id)) == 2


def test_import_suite_round_trips_an_exported_document(db_session: Any, monkeypatch: Any) -> None:
    """The pairing that makes both tools useful: export one suite, import it onto
    another connection. Checked end-to-end rather than by asserting each half's
    shape, because the shapes agreeing is the whole claim.
    """
    user = _user(db_session)
    source = _suite(db_session, user)
    _check(db_session, source, name="not null email", warn_threshold=Decimal("0.01"))
    target_connection = _suite(db_session, user).connection_id
    _as(monkeypatch, db_session, user)

    doc = server.export_suite(str(source.id))
    imported = server.import_suite(
        str(target_connection),
        name="Orders (QA)",
        checks=doc["checks"],
        description=doc["description"],
        version=doc["version"],
    )

    assert imported["check_count"] == 1
    listed = server.list_checks(imported["id"])
    assert listed["checks"][0]["name"] == "not null email"
    assert listed["checks"][0]["warn_threshold"] == 0.01


def test_import_suite_is_atomic_on_a_bad_check(db_session: Any, monkeypatch: Any) -> None:
    """A rejected document must leave nothing behind — a half-built suite would be
    worse than a clean failure, and an LLM would report partial success.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)
    before = len(suite_service.list_suites(db_session, user_id=user.id))

    with pytest.raises(ToolError):
        server.import_suite(
            str(suite.connection_id),
            name="broken",
            checks=[{"name": "x", "kind": "not_a_kind", "expectation_type": "e", "config": {}}],
        )
    assert len(suite_service.list_suites(db_session, user_id=user.id)) == before


def test_suggest_column_policy_only_suggests(db_session: Any, monkeypatch: Any) -> None:
    """It reports `saved: false` and leaves the suite's policy untouched. An LLM
    that reads a suggestion as an applied setting would tell the user their PII is
    masked when it is not.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)
    monkeypatch.setattr(
        profile_service,
        "suggest_policy_for_target",
        lambda *a, **k: {"identifier_column": "ORDER_ID", "pii_columns": ["EMAIL"]},
    )

    out = server.suggest_column_policy(str(suite.id))
    assert out["saved"] is False
    assert out["identifier_column"] == "ORDER_ID"
    assert out["pii_columns"] == ["EMAIL"]
    db_session.refresh(suite)
    assert not suite.column_policy


def test_import_suite_names_the_missing_field_instead_of_crashing(
    db_session: Any, monkeypatch: Any
) -> None:
    """`suite_io_service.import_suite` indexes required keys directly, so a hand-composed check
    omitting one raises `KeyError` — not a `DataQError`, so it escapes `_service_errors` as an
    opaque internal failure. The REST route is immune only because its Pydantic model always
    emits every key; MCP has no such model in front of it.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)

    with pytest.raises(ToolError) as exc:
        server.import_suite(
            str(suite.connection_id),
            name="partial",
            checks=[
                {
                    "name": "c",
                    "kind": "expectation",
                    "expectation_type": "expect_column_values_to_not_be_null",
                    "config": {"column": "EMAIL"},
                    # thresholds omitted — the shape an LLM composes by hand
                }
            ],
        )
    message = str(exc.value)
    assert "checks[0]" in message
    assert "warn_threshold" in message


def test_import_suite_rejects_nul_anywhere_in_the_document(
    db_session: Any, monkeypatch: Any
) -> None:
    """The screen has to cover the CHECKS, not just the suite name: `create_check`
    screens exactly these fields, and import is the second door to the same rows.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)

    def _doc(**overrides: Any) -> list[dict[str, Any]]:
        check: dict[str, Any] = {
            "name": "c",
            "kind": "expectation",
            "expectation_type": "expect_column_values_to_not_be_null",
            "config": {"column": "EMAIL"},
            "warn_threshold": None,
            "fail_threshold": None,
            "critical_threshold": None,
        }
        return [{**check, **overrides}]

    cases: list[tuple[str, list[dict[str, Any]]]] = [
        ("bad\x00name", _doc()),
        ("ok", _doc(name="check\x00name")),
        ("ok", _doc(config={"column": "EM\x00AIL"})),
    ]
    for suite_name, checks in cases:
        with pytest.raises(ToolError) as exc:
            server.import_suite(str(suite.connection_id), name=suite_name, checks=checks)
        assert "NUL" in str(exc.value)


def test_import_suite_bounds_the_suite_columns_in_the_schema() -> None:
    """`suites.name` is String(128) and `description` String(1024) — a different
    table from the per-check bounds the service validates, so the service's checks
    do not cover these. Over-length reaches Postgres and raises
    StringDataRightTruncation, escaping `_service_errors`.
    """
    import asyncio

    tool = asyncio.run(server.mcp.get_tool("import_suite"))
    assert tool is not None
    props = tool.parameters["properties"]
    assert props["name"]["maxLength"] == 128
    assert props["name"]["minLength"] == 1
    assert "1024" in str(props["description"])


# ───────── description cross-references from the #584 routing check ────────


def test_tool_descriptions_cross_reference_the_confusable_neighbours() -> None:
    """The three hesitations the 30-tool NL routing check surfaced (#584)."""
    import asyncio

    def described(name: str) -> str:
        tool = asyncio.run(server.mcp.get_tool(name))
        assert tool is not None
        return tool.description or ""

    schedules = described("list_schedules")
    notifications = described("get_notification_config")
    snooze = described("snooze_check")

    # A suite runs on a schedule OR a trigger binding; this tool sees only one.
    assert "list_trigger_bindings" in schedules

    # "Why did nobody get alerted?" is FOUR-way: config, snooze, no verdict, or deduplicated.
    assert "list_checks" in notifications and "list_runs" in notifications
    assert "dedup" in notifications.lower()
    assert "get_check_history" in notifications

    # Un-muting is served by a tool whose NAME says the opposite, so the
    # description has to carry the words a client would search for.
    assert "un-snooze" in snooze and "alerts back on" in snooze


# ────────── Tier-3A: suite target + column policy (#1424) ─────────────────


def test_update_suite_sets_a_target_and_reports_runnability(
    db_session: Any, monkeypatch: Any
) -> None:
    """The coherence gap this tool closes: `import_suite` creates a suite with NO
    target, and `trigger_suite_run` fails fast without one — so before this,
    an assistant could import a suite and had no way to make it runnable.
    """
    user = _user(db_session)
    conn_id = _suite(db_session, user).connection_id
    _as(monkeypatch, db_session, user)
    imported = server.import_suite(str(conn_id), name="fresh import", checks=[])

    # The gap, demonstrated: no target, and the run tool refuses.
    assert server.get_suite_results(imported["id"])["run"] is None
    with pytest.raises(ToolError):
        server.trigger_suite_run(imported["id"])

    # `runnable` must report BOTH states — a field that is always true would pass
    # the happy-path assertion below while telling the caller nothing.
    still_not = server.update_suite(imported["id"], name="fresh import (renamed)")
    assert still_not["target"] is None
    assert still_not["runnable"] is False

    out = server.update_suite(imported["id"], target={"table": "ORDERS_V2"})
    assert out["target"] == {"table": "ORDERS_V2"}
    assert out["runnable"] is True


def test_update_suite_leaves_omitted_fields_alone(db_session: Any, monkeypatch: Any) -> None:
    user = _user(db_session)
    suite = _suite(db_session, user)
    suite.description = "original"
    db_session.commit()
    _as(monkeypatch, db_session, user)

    out = server.update_suite(str(suite.id), name="renamed")
    assert out["name"] == "renamed"
    assert out["description"] == "original"
    assert out["target"] == {"table": "ORDERS"}


def test_update_suite_rejects_a_target_the_connection_cannot_use(
    db_session: Any, monkeypatch: Any
) -> None:
    """A flat-file target on a Snowflake connection is a clean error, not a suite
    that silently fails at run time.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)

    with pytest.raises(ToolError):
        server.update_suite(str(suite.id), target={"path": "raw/orders.csv"})
    db_session.refresh(suite)
    assert suite.target == {"table": "ORDERS"}


def test_column_policy_round_trips_through_get_and_set(db_session: Any, monkeypatch: Any) -> None:
    """The other coherence gap: `suggest_column_policy` could propose a policy that
    nothing could read back or apply.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)

    before = server.get_column_policy(str(suite.id))
    assert before["configured"] is False
    assert before["pii_columns"] == []

    server.set_column_policy(str(suite.id), pii_columns=["EMAIL"], identifier_column="ORDER_ID")
    after = server.get_column_policy(str(suite.id))
    assert after["configured"] is True
    assert after["identifier_column"] == "ORDER_ID"
    assert after["pii_columns"] == ["EMAIL"]


def test_set_column_policy_actually_changes_what_samples_show(
    db_session: Any, monkeypatch: Any
) -> None:
    """Applying the policy has to move the redaction, not just store a row — this
    is the whole point of the suggest → apply loop, and asserting on the stored
    JSONB alone would pass even if nothing consumed it.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    check = _check(db_session, suite, config={"column": "NOTE"})
    run = Run(suite_id=suite.id, status="succeeded")
    db_session.add(run)
    db_session.flush()
    db_session.add(
        Result(
            run_id=run.id,
            check_id=check.id,
            status="fail",
            sample_failures={
                "unexpected_index_list": [{"NOTE": "call me on 555-0100", "ORDER_ID": "A-1"}]
            },
        )
    )
    db_session.commit()
    _as(monkeypatch, db_session, user)

    shown = str(server.get_suite_results(str(suite.id))["checks"][0]["sample_failures"])
    assert "555-0100" in shown

    server.set_column_policy(str(suite.id), pii_columns=["NOTE"], identifier_column="ORDER_ID")
    masked = str(server.get_suite_results(str(suite.id))["checks"][0]["sample_failures"])
    assert "555-0100" not in masked
    assert "A-1" in masked, "the identifier column must stay visible to locate the row"


def test_set_column_policy_refuses_an_identifier_that_is_also_pii(
    db_session: Any, monkeypatch: Any
) -> None:
    """Masking the very column meant to locate the row is self-defeating, and the
    service rejects it — surfaced here as a clean error rather than a crash.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)

    with pytest.raises(ToolError):
        server.set_column_policy(str(suite.id), pii_columns=["EMAIL"], identifier_column="EMAIL")


def test_update_suite_nul_guard_cannot_be_shadowed_by_a_target_key(
    db_session: Any, monkeypatch: Any
) -> None:
    """The guard merged `target` over `name` in one dict, so a target key called
    "name" replaced the value being checked and the NUL reached Postgres as an
    uncaught driver error. Namespacing the buckets is the fix; this pins it.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)

    with pytest.raises(ToolError) as exc:
        server.update_suite(
            str(suite.id), name="bad\x00name", target={"name": "decoy", "table": "T"}
        )
    assert "NUL" in str(exc.value)


def test_set_column_policy_nul_guard_cannot_be_shadowed_by_a_column_name(
    db_session: Any, monkeypatch: Any
) -> None:
    """Same shadowing shape: `dict.fromkeys(pii_columns, "")` let a PII column
    literally named "identifier_column" overwrite the checked identifier.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)

    with pytest.raises(ToolError) as exc:
        server.set_column_policy(
            str(suite.id), pii_columns=["identifier_column"], identifier_column="ID\x00"
        )
    assert "NUL" in str(exc.value)


def test_update_suite_validates_the_target_shape_not_just_its_fields(
    db_session: Any, monkeypatch: Any
) -> None:
    """`suite_service` validates the target's field COMBINATION per connection
    type; `SuiteTarget` is what validates `file_format` and rejects unknown keys.
    Without routing through it, `{"file_format": "xlsx"}` saved cleanly and then
    failed every run — a config error deferred to execution.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)

    with pytest.raises(ToolError) as exc:
        server.update_suite(str(suite.id), target={"path": "raw/x.xlsx", "file_format": "xlsx"})
    assert "invalid run target" in str(exc.value)
    db_session.refresh(suite)
    assert suite.target == {"table": "ORDERS"}


def test_update_suite_triggers_auto_classify_like_the_rest_route(
    db_session: Any, monkeypatch: Any
) -> None:
    """REST parity (#634). Without it a suite imported and made runnable over MCP
    never derives a redaction policy, and captures failing samples with no row
    locator — invisible until someone reads a sample and finds nothing to
    identify the row by.
    """
    dispatched: list[Any] = []
    user = _user(db_session)
    conn_id = _suite(db_session, user).connection_id
    _as(monkeypatch, db_session, user)
    monkeypatch.setattr(run_dispatch, "dispatch_auto_classify", lambda sid: dispatched.append(sid))
    imported = server.import_suite(str(conn_id), name="needs a policy", checks=[])

    server.update_suite(imported["id"], target={"table": "ORDERS"})
    assert dispatched == [uuid.UUID(imported["id"])]

    # Never re-derived once a policy exists — that would clobber a user's own.
    dispatched.clear()
    server.set_column_policy(imported["id"], pii_columns=["EMAIL"])
    server.update_suite(imported["id"], target={"table": "ORDERS_V2"})
    assert dispatched == []


def test_column_policy_bounds_are_advertised_in_the_tool_schema() -> None:
    """The policy is walked on every read-time redaction, so an unbounded list is
    paid on every sample render rather than once at write.
    """
    import asyncio

    tool = asyncio.run(server.mcp.get_tool("set_column_policy"))
    assert tool is not None
    props = tool.parameters["properties"]
    assert props["pii_columns"]["maxItems"] == 200
    assert "255" in str(props["identifier_column"])


# ─────── Tier-3A: schedule / binding mutation + check version history ─────────


def test_update_schedule_changes_only_what_was_passed(db_session: Any, monkeypatch: Any) -> None:
    """The docstring promises an omitted argument is left "exactly as it was" —
    the claim `update_check` gets WRONG for `config` (assigned wholesale). Here
    the three arguments really are independent scalars, and this is what says so
    rather than leaving the reader to trust two tools that behave differently.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)
    created = server.create_schedule(str(suite.id), cron="0 2 * * *", timezone="America/Toronto")

    out = server.update_schedule(created["id"], cron="0 5 * * *")
    assert out["cron"] == "0 5 * * *"
    assert out["timezone"] == "America/Toronto"
    assert out["enabled"] is True


def test_update_schedule_pausing_reports_no_next_fire(db_session: Any, monkeypatch: Any) -> None:
    """The stored `next_run_at` column is still populated on a paused schedule, but the dispatcher
    filters on `enabled` and never reaches it. Reporting the raw value would name a fire time
    that will not happen — and this tool, its `create_schedule` sibling and `list_schedules`
    must not disagree about that, since an assistant may see any one of the three.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)
    created = server.create_schedule(str(suite.id), cron="0 2 * * *")
    assert created["next_run_at"] is not None

    paused = server.update_schedule(created["id"], enabled=False)
    assert paused["enabled"] is False
    assert paused["next_run_at"] is None
    # The column itself is untouched — which is exactly why the null has to be
    # produced here rather than read off the row.
    stored = schedule_service.get_schedule(db_session, uuid.UUID(created["id"]), user_id=user.id)
    assert stored.next_run_at is not None

    assert server.list_schedules(str(suite.id))[0]["next_run_at"] is None
    assert server.update_schedule(created["id"], enabled=True)["next_run_at"] is not None


def test_update_schedule_rejects_a_bad_cron_and_leaves_the_schedule_alone(
    db_session: Any, monkeypatch: Any
) -> None:
    """A rejected edit must not half-apply: the failure mode is a schedule the
    user believes they retimed that is still firing on the old cadence.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)
    created = server.create_schedule(str(suite.id), cron="0 2 * * *")

    with pytest.raises(ToolError):
        server.update_schedule(created["id"], cron="not a cron")
    db_session.rollback()
    assert server.list_schedules(str(suite.id))[0]["cron"] == "0 2 * * *"


def test_schedule_tools_reject_nul_bytes_on_both_doors(db_session: Any, monkeypatch: Any) -> None:
    """Both doors to the same two columns screen NUL — the "guard on one door but not its sibling"
    class, which was inverted here: `update_schedule` shipped with the guard and
    `create_schedule` had none.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)
    created = server.create_schedule(str(suite.id), cron="0 2 * * *")

    with pytest.raises(ToolError) as exc:
        server.update_schedule(created["id"], timezone="UTC\x00")
    assert "NUL" in str(exc.value)

    with pytest.raises(ToolError) as exc:
        server.create_schedule(str(suite.id), cron="0 2 * * *", timezone="UTC\x00")
    assert "NUL" in str(exc.value)


def test_update_schedule_free_text_is_bounded_in_the_schema() -> None:
    """Same bound as `create_schedule`, for the same reason (#567's class): an
    unbounded value reaches Postgres as a psycopg error that escapes
    `_service_errors`.
    """
    import asyncio

    tool = asyncio.run(server.mcp.get_tool("update_schedule"))
    assert tool is not None
    props = tool.parameters["properties"]
    assert "128" in str(props["cron"]) and "64" in str(props["timezone"])


def test_update_trigger_binding_toggles_without_losing_the_wiring(
    db_session: Any, monkeypatch: Any
) -> None:
    """Disabling keeps the row — which is the whole distinction the docstring
    draws against `delete_trigger_binding`, and what makes it re-enableable.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)
    created = server.create_trigger_binding(
        provider="adf", pipeline_or_dag_id="pl_nightly", env="dev", suite_id=str(suite.id)
    )

    out = server.update_trigger_binding(created["id"], enabled=False)
    assert out["enabled"] is False
    assert out["provider"] == "adf"
    assert out["pipeline_or_dag_id"] == "pl_nightly"
    assert out["suite_id"] == str(suite.id)
    # Still listed, still disabled — "not firing" is not "not wired up" (#828).
    listed = server.list_trigger_bindings(suite_id=str(suite.id))
    assert [b["enabled"] for b in listed] == [False]

    assert server.update_trigger_binding(created["id"], enabled=True)["enabled"] is True


def test_update_trigger_binding_recomputes_the_ambiguous_env_warning(
    db_session: Any, monkeypatch: Any
) -> None:
    """Advisory warnings are recomputed on ENABLE, not carried over from create:
    re-enabling is exactly when a provider/environment ambiguity regains the
    ability to lose triggers (#1186). Dropping them here would recreate the
    silent-trigger-loss incident they exist to surface.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)
    created = server.create_trigger_binding(
        provider="adf", pipeline_or_dag_id="pl_nightly", env="dev", suite_id=str(suite.id)
    )
    monkeypatch.setattr(
        trigger_binding_service,
        "_ambiguous_orchestration_warnings",
        lambda session, *, provider, env: [
            SimpleNamespace(code="ambiguous_env", message="two connections", other_envs=["qa"])
        ],
    )

    # Disabling raises none — a binding that cannot fire cannot lose a trigger.
    assert server.update_trigger_binding(created["id"], enabled=False)["warnings"] == []
    enabled = server.update_trigger_binding(created["id"], enabled=True)
    assert [w["code"] for w in enabled["warnings"]] == ["ambiguous_env"]


def test_delete_trigger_binding_removes_only_the_binding(db_session: Any, monkeypatch: Any) -> None:
    """The suite, its checks and its schedules survive — the docstring says only
    the link goes, so that is what is checked.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _check(db_session, suite)
    _as(monkeypatch, db_session, user)
    server.create_schedule(str(suite.id), cron="0 2 * * *")
    created = server.create_trigger_binding(
        provider="adf", pipeline_or_dag_id="pl_nightly", env="dev", suite_id=str(suite.id)
    )

    out = server.delete_trigger_binding(created["id"])
    assert out["deleted"] is True
    assert out["binding_id"] == created["id"]
    # Echoes exactly what re-creating it would need.
    assert (out["provider"], out["pipeline_or_dag_id"], out["env"]) == ("adf", "pl_nightly", "dev")
    assert out["suite_id"] == str(suite.id)
    assert server.list_trigger_bindings(suite_id=str(suite.id)) == []
    assert db_session.get(Suite, suite.id) is not None
    assert len(check_service.list_checks(db_session, suite.id)) == 1
    assert len(server.list_schedules(str(suite.id))) == 1


def test_binding_mutations_are_denied_on_an_inaccessible_suite(
    db_session: Any, monkeypatch: Any
) -> None:
    """A binding id alone must not be a way around suite scoping."""
    owner = _user(db_session, "owner@acme.io")
    suite = _suite(db_session, owner)
    _as(monkeypatch, db_session, owner)
    created = server.create_trigger_binding(
        provider="adf", pipeline_or_dag_id="pl_nightly", env="dev", suite_id=str(suite.id)
    )

    _as(monkeypatch, db_session, _user(db_session, "outsider@acme.io"))
    with pytest.raises(ToolError):
        server.update_trigger_binding(created["id"], enabled=False)
    with pytest.raises(ToolError):
        server.delete_trigger_binding(created["id"])


def test_list_check_versions_reports_the_snapshot_not_todays_check(
    db_session: Any, monkeypatch: Any
) -> None:
    """Edit history, not result history. Each row must describe the check AS IT
    WAS — reporting today's threshold beside an old config is precisely the
    misstatement that makes "was this edited when it started failing?"
    unanswerable.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)
    created = server.create_check(
        str(suite.id),
        name="not null email",
        expectation_type="expect_column_values_to_not_be_null",
        config={"column": "EMAIL"},
        warn_threshold=0.01,
    )
    server.update_check(str(suite.id), created["id"], warn_threshold=0.05, name="renamed")

    out = server.list_check_versions(str(suite.id), created["id"])
    versions = out["versions"]
    # Newest first, additive — the original is still there after the edit.
    assert [v["version_no"] for v in versions] == [2, 1]
    assert versions[0]["name"] == "renamed"
    assert versions[1]["name"] == "not null email"
    assert versions[1]["warn_threshold"] == pytest.approx(0.01)


def test_list_check_versions_thresholds_are_json_encodable(
    db_session: Any, monkeypatch: Any
) -> None:
    """The columns are NUMERIC and arrive as `Decimal`, which the JSON encoder refuses. REST never
    sees it because Pydantic coerces on the way out; MCP has no Pydantic in the path, which is
    exactly how #1273 happened. Asserting the Python type is not enough — `Decimal` passes
    `isinstance(x, float)` nowhere but reads fine in a repr, so this serializes.
    """
    import json

    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)
    created = server.create_check(
        str(suite.id),
        name="between",
        expectation_type="expect_column_values_to_be_between",
        config={"column": "AMOUNT", "min_value": 0, "max_value": 10},
        warn_threshold=0.01,
        fail_threshold=0.05,
        critical_threshold=0.1,
    )

    out = server.list_check_versions(str(suite.id), created["id"])
    json.dumps(out)  # would raise TypeError on a Decimal
    assert isinstance(out["versions"][0]["warn_threshold"], float)


def test_list_check_versions_truncates_honestly(db_session: Any, monkeypatch: Any) -> None:
    """A heavily-edited check returns every snapshot with its full `config`, and
    `restore_check_version` adds more.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)
    created = server.create_check(
        str(suite.id),
        name="v1",
        expectation_type="expect_column_values_to_not_be_null",
        config={"column": "EMAIL"},
    )
    for n in range(2, 6):
        server.update_check(str(suite.id), created["id"], name=f"v{n}")

    out = server.list_check_versions(str(suite.id), created["id"], limit=2)
    assert out["total"] == 5
    assert [v["version_no"] for v in out["versions"]] == [5, 4]
    # And the default returns the lot when it fits, so `total` is not a page size.
    assert len(server.list_check_versions(str(suite.id), created["id"])["versions"]) == 5


def test_create_trigger_binding_names_the_disabled_collision(
    db_session: Any, monkeypatch: Any
) -> None:
    """`uq_trigger_bindings_lookup` excludes `enabled`, so a DISABLED binding
    collides here exactly like a live one. Without the docstring naming that, an
    assistant re-wiring after a pause reads "already exists" as "the trigger is in
    place" and reports success for something that never fires.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)
    created = server.create_trigger_binding(
        provider="adf", pipeline_or_dag_id="pl_nightly", env="dev", suite_id=str(suite.id)
    )
    server.update_trigger_binding(created["id"], enabled=False)

    with pytest.raises(ToolError):
        server.create_trigger_binding(
            provider="adf", pipeline_or_dag_id="pl_nightly", env="dev", suite_id=str(suite.id)
        )
    db_session.rollback()
    tool = server.create_trigger_binding
    assert "update_trigger_binding" in (tool.__doc__ or ""), (
        "the collision guidance has to reach the LLM through the docstring — "
        "that is the only channel it reads"
    )


def test_restore_check_version_applies_the_whole_snapshot_including_nulls(
    db_session: Any, monkeypatch: Any
) -> None:
    """This is the one path that CLEARS a field. `update_check`'s PATCH convention cannot —
    omission means "leave alone" there — so restoring a version that had no warn threshold must
    actually remove today's, not skip it. The docstring says so; without this the claim is
    untested and the tool would look correct while silently leaving the old value in place.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)
    created = server.create_check(
        str(suite.id),
        name="not null email",
        expectation_type="expect_column_values_to_not_be_null",
        config={"column": "EMAIL"},
    )
    assert server.get_check(str(suite.id), created["id"])["warn_threshold"] is None
    assert server.update_check(str(suite.id), created["id"], warn_threshold=0.05)[
        "warn_threshold"
    ] == pytest.approx(0.05)

    out = server.restore_check_version(str(suite.id), created["id"], version_no=1)
    assert out["warn_threshold"] is None
    assert out["restored_from_version"] == 1


def test_restore_check_version_is_additive(db_session: Any, monkeypatch: Any) -> None:
    """History is never renumbered or deleted — the state being replaced stays
    restorable. The docstring promises that, and it is what makes a restore safe
    to offer without a confirmation step.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)
    created = server.create_check(
        str(suite.id),
        name="v1",
        expectation_type="expect_column_values_to_not_be_null",
        config={"column": "EMAIL"},
    )
    server.update_check(str(suite.id), created["id"], name="v2")

    server.restore_check_version(str(suite.id), created["id"], version_no=1)
    versions = server.list_check_versions(str(suite.id), created["id"])["versions"]
    assert [v["version_no"] for v in versions] == [3, 2, 1]
    assert versions[0]["name"] == "v1"
    # The state that was replaced is still there and still restorable.
    assert versions[1]["name"] == "v2"


def test_restore_check_version_rejects_an_unknown_version(
    db_session: Any, monkeypatch: Any
) -> None:
    """An LLM that guesses a version number instead of reading
    `list_check_versions` must get an error, not a silent no-op.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)
    created = server.create_check(
        str(suite.id),
        name="only version",
        expectation_type="expect_column_values_to_not_be_null",
        config={"column": "EMAIL"},
    )

    with pytest.raises(ToolError):
        server.restore_check_version(str(suite.id), created["id"], version_no=99)


# ── Tier 3B: assets & incidents ───────────────────────────────────────────────


def _asset(db_session: Any, *, name: str = "orders", env: str | None = "dev") -> Asset:
    asset = Asset(namespace="snowflake://acct/RETAIL", name=name, env=env)
    db_session.add(asset)
    db_session.commit()
    return asset


def _incident(db_session: Any, *, asset: Asset, check: Check, suite: Suite, **kw: Any) -> Incident:
    incident = Incident(
        asset_id=asset.id,
        check_id=check.id,
        suite_id=suite.id,
        status=kw.pop("status", "open"),
        **kw,
    )
    db_session.add(incident)
    db_session.commit()
    return incident


def test_list_assets_reports_an_unmonitored_asset_as_unmonitored(
    db_session: Any, monkeypatch: Any
) -> None:
    """The failure this field exists to prevent: an asset with no suite has
    `worst_severity: null` and `checks_total: 0`, which is literally "nothing is
    failing" and reads as a clean bill of health. `monitored` names the actual
    state so the two cannot be confused.
    """
    user = _user(db_session)
    _asset(db_session, name="unwatched")
    _as(monkeypatch, db_session, user)

    out = server.list_assets()
    row = next(a for a in out["assets"] if a["name"] == "unwatched")
    assert row["monitored"] is False
    assert row["suite_count"] == 0
    assert row["worst_severity"] is None
    assert row["checks_total"] == 0


def test_list_assets_reports_truncation_against_the_real_total(
    db_session: Any, monkeypatch: Any
) -> None:
    """`truncated` is computed from the workspace total, not inferred from
    `len(page) == limit` — which is wrong on the exact-boundary page, the one
    case where a client would confidently report a partial list as complete.
    """
    user = _user(db_session)
    for i in range(3):
        _asset(db_session, name=f"tbl_{i}")
    _as(monkeypatch, db_session, user)

    page = server.list_assets(limit=2)
    assert page["total"] == 3
    assert page["returned"] == 2
    assert page["truncated"] is True

    # The exact-boundary page: full, and yet nothing follows it.
    boundary = server.list_assets(limit=3)
    assert boundary["returned"] == 3
    assert boundary["truncated"] is False


def test_get_asset_counts_the_suites_it_cannot_name(db_session: Any, monkeypatch: Any) -> None:
    """ADR 0037's split, asserted: the summary aggregates over EVERY composing
    suite, while `suites` lists only what the caller may view. Without
    `restricted_suite_count` an LLM would present the visible suites as the whole
    explanation for a workspace-true number.
    """
    owner = _user(db_session)
    outsider = _user(db_session, email="outsider@acme.io")
    asset = _asset(db_session)
    suite = _suite(db_session, owner)
    suite.asset_id = asset.id
    db_session.commit()
    _as(monkeypatch, db_session, outsider)

    out = server.get_asset(str(asset.id))
    assert out["summary"]["suite_count"] == 1
    assert out["suites"] == []
    assert out["restricted_suite_count"] == 1


def test_get_asset_qualifies_lineage_when_a_source_is_failing(
    db_session: Any, monkeypatch: Any
) -> None:
    """An empty lineage graph behind a broken poller must never read as "nothing
    feeds this table" (#828). The qualifier is the only thing that distinguishes
    the two, so it is asserted rather than trusted.
    """
    user = _user(db_session)
    asset = _asset(db_session)
    _as(monkeypatch, db_session, user)

    failing = SimpleNamespace(
        connection_id=uuid.uuid4(),
        name="warehouse-dev",
        type="snowflake",
        consecutive_failures=4,
        last_error="credential_expired",
        last_polled_at=None,
    )
    monkeypatch.setattr(asset_view_service, "failing_lineage_sources", lambda _s: [failing])

    out = server.get_asset(str(asset.id))
    assert out["lineage"]["upstream"] == []
    assert any("warehouse-dev" in q for q in out["lineage"]["qualified_by"])


def test_list_incidents_rejects_an_unknown_status(db_session: Any, monkeypatch: Any) -> None:
    """A typo'd status returning `[]` would answer "what's broken?" with
    "nothing" — the #828 shape on the question where it is worst.
    """
    user = _user(db_session)
    _as(monkeypatch, db_session, user)

    with pytest.raises(ToolError):
        server.list_incidents(status="opened")


def test_list_incidents_hides_incidents_on_suites_the_caller_cannot_see(
    db_session: Any, monkeypatch: Any
) -> None:
    owner = _user(db_session)
    outsider = _user(db_session, email="nope@acme.io")
    asset = _asset(db_session)
    suite = _suite(db_session, owner)
    check = Check(suite_id=suite.id, name="freshness", expectation_type="expect_x", config={})
    db_session.add(check)
    db_session.commit()
    _incident(db_session, asset=asset, check=check, suite=suite)

    _as(monkeypatch, db_session, owner)
    assert server.list_incidents()["total"] == 1

    _as(monkeypatch, db_session, outsider)
    assert server.list_incidents()["total"] == 0


def test_get_incident_hides_an_incident_on_an_invisible_suite(
    db_session: Any, monkeypatch: Any
) -> None:
    """404-no-leak: an outsider cannot tell a real incident from a fictional one."""
    owner = _user(db_session)
    outsider = _user(db_session, email="nope2@acme.io")
    asset = _asset(db_session)
    suite = _suite(db_session, owner)
    check = Check(suite_id=suite.id, name="c", expectation_type="expect_x", config={})
    db_session.add(check)
    db_session.commit()
    incident = _incident(db_session, asset=asset, check=check, suite=suite)

    _as(monkeypatch, db_session, outsider)
    with pytest.raises(ToolError) as real:
        server.get_incident(str(incident.id))
    with pytest.raises(ToolError) as fictional:
        server.get_incident(str(uuid.uuid4()))
    assert str(real.value) == str(fictional.value)


def test_get_incident_returns_the_evidence_card(db_session: Any, monkeypatch: Any) -> None:
    """The evidence card is what makes this tool worth more than `list_incidents`,
    and its `check`/`asset` layers are what the summary fields are lifted from.
    """
    owner = _user(db_session)
    asset = _asset(db_session)
    suite = _suite(db_session, owner)
    check = Check(suite_id=suite.id, name="row count", expectation_type="expect_x", config={})
    db_session.add(check)
    db_session.commit()
    incident = _incident(
        db_session,
        asset=asset,
        check=check,
        suite=suite,
        evidence={
            "check": {"name": "row count"},
            "asset": {"namespace": asset.namespace, "name": asset.name},
            "failing_result": {"status": "fail"},
            "profile_diff": None,
        },
    )
    _as(monkeypatch, db_session, owner)

    out = server.get_incident(str(incident.id))
    assert out["check_name"] == "row count"
    assert out["asset_name"] == "orders"
    assert out["latest_severity"] == "fail"
    assert out["evidence"]["profile_diff"] is None


def test_get_incident_redacts_a_same_asset_sibling_from_an_invisible_suite(
    db_session: Any, monkeypatch: Any
) -> None:
    """#1635: `same_asset_siblings` is stored workspace-true (no caller in
    scope at build time); the read surface must withhold a sibling suite the
    caller has no grant on, not just the incident's own suite.
    """
    owner = _user(db_session)
    suite = _suite(db_session, owner)
    other_suite = _suite(db_session, owner)  # the sibling result lives here
    asset = _asset(db_session)
    check = Check(suite_id=suite.id, name="c", expectation_type="expect_x", config={})
    db_session.add(check)
    db_session.commit()
    incident = _incident(
        db_session,
        asset=asset,
        check=check,
        suite=suite,
        evidence={
            "same_asset_siblings": [
                {
                    "check_id": str(uuid.uuid4()),
                    "check_name": "orders_volume_ok",
                    "kind": "volume",
                    "suite_id": str(other_suite.id),
                    "status": "fail",
                    "metric_value": -60.0,
                    "created_at": None,
                }
            ]
        },
    )
    caller = _user(db_session, email="caller@acme.io")
    db_session.add(Share(suite_id=suite.id, user_id=caller.id, permission="view"))
    db_session.commit()
    _as(monkeypatch, db_session, caller)

    out = server.get_incident(str(incident.id))
    assert out["evidence"]["same_asset_siblings"] == []
    assert out["evidence"]["same_asset_siblings_restricted_count"] == 1


def test_list_incidents_rejects_an_unknown_asset_id(db_session: Any, monkeypatch: Any) -> None:
    """The sibling of the `status` guard. A well-formed but unknown asset id would
    otherwise return an empty page, which reads as "nothing is broken on that
    asset" (#828). Asset identity is workspace-visible (ADR 0037), so naming an id
    as unknown leaks nothing.
    """
    user = _user(db_session)
    _as(monkeypatch, db_session, user)

    with pytest.raises(ToolError):
        server.list_incidents(asset_id=str(uuid.uuid4()))


def test_list_incidents_since_hours_filters_on_last_seen_at(
    db_session: Any, monkeypatch: Any
) -> None:
    """#1442: filters on `last_seen_at` (the most recent breach), not
    `created_at` — an incident opened long ago that breached again recently
    must still surface within a short window, and one that hasn't breached
    recently must not, regardless of how old it is.
    """
    owner = _user(db_session)
    asset = _asset(db_session)
    suite = _suite(db_session, owner)
    check = Check(suite_id=suite.id, name="c", expectation_type="expect_x", config={})
    db_session.add(check)
    db_session.commit()
    now = datetime.now(UTC)
    recently_breached = _incident(
        db_session,
        asset=asset,
        check=check,
        suite=suite,
        created_at=now - timedelta(hours=100),
        last_seen_at=now - timedelta(hours=1),
    )
    _incident(
        db_session,
        asset=asset,
        check=check,
        suite=suite,
        status="resolved",
        created_at=now - timedelta(hours=100),
        last_seen_at=now - timedelta(hours=100),
    )
    _as(monkeypatch, db_session, owner)

    out = server.list_incidents(since_hours=24)
    assert out["total"] == 1
    assert out["incidents"][0]["id"] == str(recently_breached.id)


def test_list_incidents_rejects_until_hours_not_less_than_since_hours(
    db_session: Any, monkeypatch: Any
) -> None:
    user = _user(db_session)
    _as(monkeypatch, db_session, user)

    with pytest.raises(ToolError):
        server.list_incidents(since_hours=24, until_hours=48)


def test_ack_incident_records_the_actor_and_leaves_it_unresolved(
    db_session: Any, monkeypatch: Any
) -> None:
    """Acknowledging is explicitly NOT resolving — the docstring says the check
    still runs and still alerts, and the status is what an LLM will report.
    """
    owner = _user(db_session)
    asset = _asset(db_session)
    suite = _suite(db_session, owner)
    check = Check(suite_id=suite.id, name="c", expectation_type="expect_x", config={})
    db_session.add(check)
    db_session.commit()
    incident = _incident(db_session, asset=asset, check=check, suite=suite)
    _as(monkeypatch, db_session, owner)

    out = server.ack_incident(str(incident.id), note="looking at it")
    assert out["status"] == "acknowledged"
    assert out["acknowledged_at"] is not None
    assert out["resolved_at"] is None


def test_resolve_incident_refuses_a_second_resolve(db_session: Any, monkeypatch: Any) -> None:
    """A resolved incident is closed for good — the next breach opens a NEW one.
    A silent second resolve would let an assistant report a fresh action that
    never happened.
    """
    owner = _user(db_session)
    asset = _asset(db_session)
    suite = _suite(db_session, owner)
    check = Check(suite_id=suite.id, name="c", expectation_type="expect_x", config={})
    db_session.add(check)
    db_session.commit()
    incident = _incident(db_session, asset=asset, check=check, suite=suite)
    _as(monkeypatch, db_session, owner)

    assert server.resolve_incident(str(incident.id))["status"] == "resolved"
    with pytest.raises(ToolError):
        server.resolve_incident(str(incident.id))


def test_ack_incident_refuses_a_resolved_incident(db_session: Any, monkeypatch: Any) -> None:
    owner = _user(db_session)
    asset = _asset(db_session)
    suite = _suite(db_session, owner)
    check = Check(suite_id=suite.id, name="c", expectation_type="expect_x", config={})
    db_session.add(check)
    db_session.commit()
    incident = _incident(db_session, asset=asset, check=check, suite=suite, status="resolved")
    _as(monkeypatch, db_session, owner)

    with pytest.raises(ToolError):
        server.ack_incident(str(incident.id))


def test_get_near_misses_reports_both_envs(db_session: Any, monkeypatch: Any) -> None:
    """The mismatch IS the finding — a row carrying only one env would be
    unactionable, since the fix is to change one of the two.
    """
    owner = _user(db_session)
    suite = _suite(db_session, owner)
    _as(monkeypatch, db_session, owner)

    record = SimpleNamespace(
        provider="airflow",
        pipeline_or_dag_id="nightly_load",
        run_env="prod",
        binding_env="qa",
        updated_at=datetime.now(UTC),
    )
    monkeypatch.setattr(orchestration_service, "list_env_near_misses", lambda *a, **k: [record])

    out = server.get_near_misses(str(suite.id))["near_misses"]
    assert out[0]["run_env"] == "prod"
    assert out[0]["binding_env"] == "qa"
    assert out[0]["pipeline_or_dag_id"] == "nightly_load"


def test_list_columns_defaults_to_the_suite_target(db_session: Any, monkeypatch: Any) -> None:
    """The whole point of the defaulting: 'what columns are on this suite?'
    should not require the caller to already know the table name.
    """
    owner = _user(db_session)
    suite = _suite(db_session, owner)
    _as(monkeypatch, db_session, owner)

    seen: dict[str, Any] = {}

    def _fake(connection: Any, **kw: Any) -> list[str]:
        seen.update(kw)
        return ["ORDER_ID", "EMAIL"]

    monkeypatch.setattr(profile_service, "list_columns", _fake)

    out = server.list_columns(str(suite.id))
    assert out["columns"] == ["ORDER_ID", "EMAIL"]
    assert seen["table"] == "ORDERS"


def test_list_columns_reports_a_suite_with_no_target(db_session: Any, monkeypatch: Any) -> None:
    """An actionable error, not an empty column list — an empty list would read as
    'this table has no columns' and send an assistant off to guess names.
    """
    owner = _user(db_session)
    suite = _suite(db_session, owner, with_target=False)
    _as(monkeypatch, db_session, owner)

    with pytest.raises(ToolError):
        server.list_columns(str(suite.id))


# ── honesty-field pass (all 46 tools) ────────────────────────────────────────


def test_run_results_report_how_much_of_the_sample_was_masked(
    db_session: Any, monkeypatch: Any
) -> None:
    """The REST route has returned `redaction`/`redacted_columns` since #1115 and
    MCP dropped them, so a masked sample was indistinguishable from an unmasked
    one. Both readings are confident and wrong: mask tokens reported as data, or
    a fully-masked sample reported as 'no failing rows were captured'.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    suite.column_policy = {"identifier_column": "ORDER_ID", "pii_columns": ["EMAIL"]}
    check = Check(
        suite_id=suite.id,
        name="email not null",
        expectation_type="expect_column_values_to_not_be_null",
        config={"column": "EMAIL"},
    )
    db_session.add(check)
    db_session.commit()
    run = Run(suite_id=suite.id, status="succeeded")
    db_session.add(run)
    db_session.commit()
    db_session.add(
        Result(
            run_id=run.id,
            check_id=check.id,
            status="fail",
            sample_failures={
                "partial_unexpected_list": [{"ORDER_ID": "A-1", "EMAIL": "ada@acme.io"}]
            },
        )
    )
    db_session.commit()
    _as(monkeypatch, db_session, user)

    row = server.get_run_results(str(run.id))["checks"][0]
    assert row["redaction"] in {"full", "partial", "none"}
    assert "EMAIL" in row["redacted_columns"]
    # And the id, so the model can act on the check it just found.
    assert row["check_id"] == str(check.id)


def test_get_run_status_marks_a_running_run_as_not_final(db_session: Any, monkeypatch: Any) -> None:
    """`counts` on a mid-run suite is progress, not a verdict. The sibling tools
    have carried `results_final` since #318; this one emitted the counts bare, so
    a 30-check suite three checks in reported `{"pass": 3}` — the tool's own
    definition of 'nothing failed', about a run that has barely started.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    db_session.add(Check(suite_id=suite.id, name="c", expectation_type="expect_x", config={}))
    db_session.commit()
    running = Run(suite_id=suite.id, status="running")
    done = Run(suite_id=suite.id, status="succeeded")
    db_session.add_all([running, done])
    db_session.commit()
    _as(monkeypatch, db_session, user)

    assert server.get_run_status(str(running.id))["results_final"] is False
    assert server.get_run_status(str(done.id))["results_final"] is True


def test_get_check_history_flags_a_truncated_page_and_its_window(
    db_session: Any, monkeypatch: Any
) -> None:
    """A count-capped page answering a time-shaped question ('when did this start
    failing?'). Without `truncated`, the oldest point is a page boundary reported
    as an onset — wrong whenever the failure predates the page.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    check = Check(suite_id=suite.id, name="c", expectation_type="expect_x", config={})
    db_session.add(check)
    db_session.commit()
    for _ in range(3):
        run = Run(suite_id=suite.id, status="succeeded")
        db_session.add(run)
        db_session.commit()
        db_session.add(Result(run_id=run.id, check_id=check.id, status="pass"))
        db_session.commit()
    _as(monkeypatch, db_session, user)

    page = server.get_check_history(str(suite.id), str(check.id), limit=2)
    assert page["truncated"] is True
    assert page["oldest_in_page"] is not None and page["newest_in_page"] is not None

    whole = server.get_check_history(str(suite.id), str(check.id), limit=50)
    assert whole["truncated"] is False

    # The exact-boundary page: full, and yet nothing follows it.
    boundary = server.get_check_history(str(suite.id), str(check.id), limit=3)
    assert boundary["total"] == 3
    assert len(boundary["points"]) == 3
    assert boundary["truncated"] is False


def test_list_runs_reports_the_time_window_it_actually_covered(
    db_session: Any, monkeypatch: Any
) -> None:
    """#1442: there is no time filter, and the questions are time-shaped. The
    covered interval is returned so a model can check whether the period it was
    asked about is inside the page instead of calling the page 'today'.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    for _ in range(2):
        db_session.add(Run(suite_id=suite.id, status="succeeded"))
    db_session.commit()
    _as(monkeypatch, db_session, user)

    out = server.list_runs(limit=1)
    assert out["total"] == 2
    assert out["returned"] == 1
    assert out["newest_in_page"] is not None
    assert out["oldest_in_page"] == out["newest_in_page"]


def test_get_pipeline_status_reports_truncation(db_session: Any, monkeypatch: Any) -> None:
    """It took `limit` and returned a bare list, so a full page was
    indistinguishable from the whole set on 'did any pipeline fail overnight?'.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    _as(monkeypatch, db_session, user)
    for i in range(3):
        db_session.add(
            PipelineRun(
                provider="adf",
                connection_id=suite.connection_id,
                pipeline_or_dag_id=f"pl_{i}",
                provider_run_id=str(uuid.uuid4()),
                env="dev",
                status="succeeded",
            )
        )
    db_session.commit()

    page = server.get_pipeline_status(limit=2)
    assert page["total"] == 3
    assert page["truncated"] is True
    assert len(page["pipeline_runs"]) == 2

    with pytest.raises(ToolError):
        server.get_pipeline_status(status="explode")


def test_get_adf_pipeline_status_is_a_working_alias_for_get_pipeline_status(
    db_session: Any, monkeypatch: Any
) -> None:
    """#1443: the old name is kept registered (a client with it pinned must not
    break) but must delegate to the real tool rather than diverge from it.
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    db_session.add(
        PipelineRun(
            provider="airflow",
            connection_id=suite.connection_id,
            pipeline_or_dag_id="flow_a",
            provider_run_id=str(uuid.uuid4()),
            env="dev",
            status="succeeded",
        )
    )
    db_session.commit()
    _as(monkeypatch, db_session, user)

    assert server.get_adf_pipeline_status() == server.get_pipeline_status()


def test_import_suite_reports_that_it_is_not_runnable(db_session: Any, monkeypatch: Any) -> None:
    """An export document carries no run target, so an imported suite cannot run
    until `update_suite` gives it one. The tool that creates that state now says
    so, instead of leaving it to be discovered when `trigger_suite_run` fails.
    """
    user = _user(db_session)
    conn = _suite(db_session, user).connection_id
    _as(monkeypatch, db_session, user)

    out = server.import_suite(connection_id=str(conn), name="imported", checks=[])
    assert out["runnable"] is False
    assert out["target"] is None


def test_incident_payload_exposes_the_reopen_link(db_session: Any, monkeypatch: Any) -> None:
    """`resolve_incident` promises the next breach opens a new incident 'linked
    back to this one', and nothing returned the link — so a recurrence of a
    weekly problem read as brand new.
    """
    owner = _user(db_session)
    asset = _asset(db_session)
    suite = _suite(db_session, owner)
    check = Check(suite_id=suite.id, name="c", expectation_type="expect_x", config={})
    db_session.add(check)
    db_session.commit()
    first = _incident(db_session, asset=asset, check=check, suite=suite, status="resolved")
    second = _incident(db_session, asset=asset, check=check, suite=suite)
    second.prior_incident_id = first.id
    db_session.commit()
    _as(monkeypatch, db_session, owner)

    out = server.get_incident(str(second.id))
    assert out["prior_incident_id"] == str(first.id)
    assert out["is_recurrence"] is True
    assert server.get_incident(str(first.id))["is_recurrence"] is False


def test_update_suite_surfaces_its_own_side_effects(db_session: Any, monkeypatch: Any) -> None:
    """Both signals previously went to the server log only — and the caller who
    just re-pointed the suite is the one person who can act on them.
    """
    user = _user(db_session)
    suite = _suite(db_session, user, with_target=False)
    _as(monkeypatch, db_session, user)
    monkeypatch.setattr(run_dispatch, "dispatch_auto_classify", lambda _sid: None)

    fresh = server.update_suite(str(suite.id), target={"table": "ORDERS"})
    assert fresh["column_policy_pending"] is True
    assert fresh["column_policy_may_be_stale"] is False

    db_session.get(Suite, suite.id).column_policy = {"pii_columns": ["EMAIL"]}
    db_session.commit()
    moved = server.update_suite(str(suite.id), target={"table": "ORDERS_V2"})
    assert moved["column_policy_may_be_stale"] is True


def test_every_paged_tool_reports_truncation_the_same_way(
    db_session: Any, monkeypatch: Any
) -> None:
    """A client is told to branch on `truncated`. Where a paged tool omits it, the
    absence reads as `false` and a capped page is reported as the whole set —
    the exact failure the field exists to prevent (#1449 review).
    """
    user = _user(db_session)
    suite = _suite(db_session, user)
    check = Check(
        suite_id=suite.id,
        name="c",
        expectation_type="expect_column_values_to_not_be_null",
        config={"column": "EMAIL"},
    )
    db_session.add(check)
    db_session.commit()
    run = Run(suite_id=suite.id, status="succeeded")
    db_session.add(run)
    db_session.commit()
    db_session.add(Result(run_id=run.id, check_id=check.id, status="pass"))
    db_session.commit()
    _as(monkeypatch, db_session, user)

    paged = {
        "list_runs": server.list_runs(),
        "list_checks": server.list_checks(str(suite.id)),
        "list_check_versions": server.list_check_versions(str(suite.id), str(check.id)),
        "list_incidents": server.list_incidents(),
        "list_assets": server.list_assets(),
        "get_check_history": server.get_check_history(str(suite.id), str(check.id)),
        "get_pipeline_status": server.get_pipeline_status(),
    }
    for name, payload in paged.items():
        assert "total" in payload, f"{name} pages but reports no total"
        assert "truncated" in payload, (
            f"{name} pages but reports no `truncated` — a client branching on it "
            "reads the absence as false and calls a capped page complete"
        )
        assert isinstance(payload["truncated"], bool)


def test_profile_column_applies_the_governance_tag_floor_on_the_default_target(
    db_session: Any, monkeypatch: Any
) -> None:
    """A warehouse tag is the only thing marking this column — it must apply."""
    from backend.app.db.models import Asset
    from backend.app.services.profile_service import ColumnProfile, ProfileResult

    user = _user(db_session)
    suite = _suite(db_session, user)
    asset = Asset(
        namespace="db.schema",
        name="ORDERS",
        env="dev",
        connection_id=suite.connection_id,
        # `field_7` is un-guessable by name and holds innocuous-looking values —
        # the tag is the ONLY thing that marks it.
        column_tags={"field_7": "restricted"},
    )
    db_session.add(asset)
    db_session.flush()
    suite.asset_id = asset.id
    db_session.flush()

    def _fake_profile(connection: Any, **kwargs: Any) -> ProfileResult:
        return ProfileResult(
            row_count=1,
            table=kwargs["table"],
            schema=kwargs["schema"],
            catalog=None,
            path=None,
            file_format=None,
            columns=[
                ColumnProfile(
                    column="field_7",
                    null_count=0,
                    null_fraction=0.0,
                    distinct_count=2,
                    min_value="aaa",
                    max_value="zzz",
                    top_values=[{"value": "aaa", "count": 1}],
                )
            ],
        )

    monkeypatch.setattr(profile_service, "profile_connection", _fake_profile)
    _as(monkeypatch, db_session, user)

    out = server.profile_column(str(suite.id), columns=["field_7"])
    assert out["redacted_columns"] == ["field_7"]
    assert out["columns"][0]["min_value"] is None
    assert [t["value"] for t in out["columns"][0]["top_values"]] == ["<redacted>"]


def test_profile_column_honours_a_clearance_on_the_suites_own_target(
    db_session: Any, monkeypatch: Any
) -> None:
    """Target defaulting must not silently discard the tag CLEARANCES (F1)."""
    from backend.app.db.models import Asset
    from backend.app.services.profile_service import ColumnProfile, ProfileResult

    user = _user(db_session)
    suite = _suite(db_session, user)
    suite.column_policy = {"require_classification": True}
    asset = Asset(
        namespace="db.schema",
        name="ORDERS",
        env="dev",
        connection_id=suite.connection_id,
        column_tags={"region_code": "public"},
    )
    db_session.add(asset)
    db_session.flush()
    suite.asset_id = asset.id
    db_session.flush()

    def _fake_profile(connection: Any, **kwargs: Any) -> ProfileResult:
        return ProfileResult(
            row_count=1,
            table=kwargs["table"],
            schema=kwargs["schema"],
            catalog=None,
            path=None,
            file_format=None,
            columns=[
                ColumnProfile(
                    column="region_code",
                    null_count=0,
                    null_fraction=0.0,
                    distinct_count=2,
                    min_value="EMEA",
                    max_value="NA",
                    top_values=[{"value": "EMEA", "count": 1}],
                )
            ],
        )

    monkeypatch.setattr(profile_service, "profile_connection", _fake_profile)
    _as(monkeypatch, db_session, user)

    out = server.profile_column(str(suite.id), columns=["region_code"])
    # The clearance survived defaulting, so the cleared column keeps its values.
    assert out["redacted_columns"] == []
    assert out["columns"][0]["min_value"] == "EMEA"
