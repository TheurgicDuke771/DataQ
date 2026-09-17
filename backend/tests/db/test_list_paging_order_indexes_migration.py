"""Up/down + planner tests for `d416af4f8ad3_add_list_paging_order_indexes` (#1245)."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text

from backend.app.db.models import Incident, PipelineRun, Run
from backend.app.services import incident_service, orchestration_service
from backend.app.services import run_service as run_svc

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "d416af4f8ad3_add_list_paging_order_indexes.py"
)

_RUNS_INDEX = "ix_runs_created_id"
_PIPELINE_INDEX = "ix_pipeline_runs_created_id"
_INCIDENTS_INDEX = "ix_incidents_last_seen_id"

_USER_ID = uuid.UUID("55555555-5555-4555-8555-000000000001")


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("_list_paging_order_migration", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _indexdef(connection: Any, name: str) -> str | None:
    indexdef: str | None = connection.execute(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = :n"),
        {"n": name},
    ).scalar_one_or_none()
    return indexdef


def _plan(connection: Any, statement: Any) -> str:
    compiled = statement.compile(bind=connection.engine, compile_kwargs={"literal_binds": True})
    return "\n".join(row[0] for row in connection.execute(text(f"EXPLAIN {compiled}")))


def _runs_list_statement() -> Any:
    return (
        select(Run)
        .where(
            *run_svc._run_filters(user_id=_USER_ID, suite_id=None, status=None, include_all=False)
        )
        .order_by(Run.created_at.desc(), Run.id.desc())
        .limit(50)
    )


def _pipeline_runs_list_statement() -> Any:
    return (
        select(PipelineRun)
        .where(*orchestration_service._pipeline_run_filters(provider=None, status=None))
        .order_by(*orchestration_service.pipeline_run_order_by())
        .limit(50)
    )


def _incidents_list_statement() -> Any:
    return (
        select(Incident)
        .where(
            *incident_service._incident_filters(
                user_id=_USER_ID,
                include_all=False,
                asset_id=None,
                suite_id=None,
                state=None,
            )
        )
        .order_by(Incident.last_seen_at.desc(), Incident.id.desc())
        .limit(100)
    )


def test_revision_chain() -> None:
    module = _load_migration()
    assert module.revision == "d416af4f8ad3"
    assert module.down_revision == "b4e91c7a0d38"


def test_create_all_builds_every_index(db_session: Any) -> None:
    connection = db_session.connection()
    for name in (_RUNS_INDEX, _PIPELINE_INDEX, _INCIDENTS_INDEX):
        assert _indexdef(connection, name) is not None, f"{name} is missing"


def test_index_columns_match_the_page_order(db_session: Any) -> None:
    """`incidents` pages by `last_seen_at`, not `created_at` — an index on the wrong
    column would be built, be reported present, and never be used by the list.
    """
    connection = db_session.connection()
    assert "(created_at DESC, id DESC)" in (_indexdef(connection, _RUNS_INDEX) or "")
    assert "(created_at DESC, id DESC)" in (_indexdef(connection, _PIPELINE_INDEX) or "")
    assert "(last_seen_at DESC, id DESC)" in (_indexdef(connection, _INCIDENTS_INDEX) or "")


def test_up_down_up(db_session: Any) -> None:
    """Executes the exact statement strings `upgrade()`/`downgrade()` run (CONCURRENTLY
    stripped — it cannot run inside this fixture's open transaction), so a wrong name or a
    narrowed loop in either function goes red here rather than being re-derived by the test.
    """
    module = _load_migration()
    connection = db_session.connection()
    drops = [sql.replace("CONCURRENTLY ", "") for sql in module._drop_sql()]
    creates = [sql.replace("CONCURRENTLY ", "") for sql in module._create_sql()]
    names = [name for name, _table, _columns in module._INDEXES]
    assert names == [_RUNS_INDEX, _PIPELINE_INDEX, _INCIDENTS_INDEX]
    for name, drop_sql, create_sql in zip(names, drops, creates, strict=True):
        assert name in drop_sql and name in create_sql
        for _ in range(2):
            assert _indexdef(connection, name) is not None
            connection.execute(text(drop_sql))
            assert _indexdef(connection, name) is None
            connection.execute(text(create_sql))
        assert _indexdef(connection, name) is not None


def test_downgrade_drops_every_index_upgrade_creates(db_session: Any) -> None:
    """`downgrade()` must not silently leave one behind. Driven off the module's own
    statement builders, not off a second list written here.
    """
    module = _load_migration()
    connection = db_session.connection()
    for sql in module._drop_sql():
        connection.execute(text(sql.replace("CONCURRENTLY ", "")))
    for name in (_RUNS_INDEX, _PIPELINE_INDEX, _INCIDENTS_INDEX):
        assert _indexdef(connection, name) is None, f"downgrade() left {name} behind"


def test_runs_list_uses_the_index(db_session: Any) -> None:
    """The unfiltered `/runs` page — `suite_id IN (<accessible suites>)`, which
    `ix_runs_suite_created` cannot serve because it leads with `suite_id`.

    `enable_seqscan`/`enable_sort = off` remove the empty-test-table cost preference for a
    scan-then-sort plan; they cannot conjure an ordered path out of an index whose column
    order does not match the ORDER BY — with the index absent the planner sorts anyway.
    """
    connection = db_session.connection()
    connection.execute(text("SET LOCAL enable_seqscan = off"))
    connection.execute(text("SET LOCAL enable_sort = off"))
    plan = _plan(connection, _runs_list_statement())
    assert _RUNS_INDEX in plan, f"the /runs page did not use {_RUNS_INDEX}:\n{plan}"


def test_pipeline_runs_list_uses_the_index(db_session: Any) -> None:
    connection = db_session.connection()
    connection.execute(text("SET LOCAL enable_seqscan = off"))
    plan = _plan(connection, _pipeline_runs_list_statement())
    assert (
        _PIPELINE_INDEX in plan
    ), f"the /pipeline_runs page did not use {_PIPELINE_INDEX}:\n{plan}"


def test_incidents_list_uses_the_index(db_session: Any) -> None:
    connection = db_session.connection()
    connection.execute(text("SET LOCAL enable_seqscan = off"))
    connection.execute(text("SET LOCAL enable_sort = off"))
    plan = _plan(connection, _incidents_list_statement())
    assert _INCIDENTS_INDEX in plan, f"the /incidents page did not use {_INCIDENTS_INDEX}:\n{plan}"


def test_incident_count_is_not_claimed_to_use_the_page_index(db_session: Any) -> None:
    """The `X-Total-Count` COUNT is deliberately NOT served by these indexes — it has no
    ORDER BY. Recorded as a test so the "the COUNT now dominates page 1" finding cannot
    quietly become false.

    `enable_seqscan = off` is what gives the assertion teeth: without it the empty test
    table always seq-scans and the claim holds no matter which indexes exist. With every
    index on the table available and a scan forbidden, the planner still reaches for a
    suite-leading one.
    """
    connection = db_session.connection()
    connection.execute(text("SET LOCAL enable_seqscan = off"))
    statement = (
        select(func.count())
        .select_from(Incident)
        .where(
            *incident_service._incident_filters(
                user_id=_USER_ID,
                include_all=False,
                asset_id=None,
                suite_id=None,
                state=None,
            )
        )
    )
    plan = _plan(connection, statement)
    assert _INCIDENTS_INDEX not in plan, plan
    assert "ix_incidents_suite_id" in plan, plan
