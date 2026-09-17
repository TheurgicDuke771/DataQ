"""Up/down + planner tests for `b4e91c7a0d38_add_runs_triggered_by_index` (#1715)."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import text

from backend.app.db.models import Connection, Run, Suite, User
from backend.app.orchestration import markers

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "b4e91c7a0d38_add_runs_triggered_by_index.py"
)
_INDEX = "ix_runs_triggered_by"
_PIPELINE_INDEX = "ix_pipeline_runs_marker"


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("_runs_triggered_by_migration", _MIGRATION_PATH)
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


def _assert_shape(indexdef: str | None) -> None:
    assert indexdef is not None, f"{_INDEX} is missing"
    head, sep, predicate = indexdef.partition(" WHERE ")
    assert sep, f"{_INDEX} is not a partial index: {indexdef}"
    assert "(triggered_by)" in head, indexdef
    assert "IS NOT NULL" in predicate, indexdef


def _plan(connection: Any, statement: Any) -> str:
    compiled = statement.compile(bind=connection.engine, compile_kwargs={"literal_binds": True})
    return "\n".join(row[0] for row in connection.execute(text(f"EXPLAIN {compiled}")))


def test_revision_chain() -> None:
    module = _load_migration()
    assert module.revision == "b4e91c7a0d38"
    assert module.down_revision == "3d7c1a9fb204"


def test_create_all_builds_the_partial_index(db_session: Any) -> None:
    _assert_shape(_indexdef(db_session.connection(), _INDEX))


def test_up_down_up(db_session: Any) -> None:
    """Runs the migration module's OWN statements (CONCURRENTLY stripped — it cannot run in
    this fixture's open transaction), so a wrong index name in `downgrade()` goes red here.
    """
    module = _load_migration()
    create_sql = module._CREATE_SQL.replace("CONCURRENTLY ", "")
    drop_sql = module._DROP_SQL.replace("CONCURRENTLY ", "")
    assert _INDEX in drop_sql, f"downgrade() drops the wrong index: {drop_sql}"
    connection = db_session.connection()
    for _ in range(2):
        _assert_shape(_indexdef(connection, _INDEX))
        connection.execute(text(drop_sql))
        assert _indexdef(connection, _INDEX) is None
        connection.execute(text(create_sql))
    _assert_shape(_indexdef(connection, _INDEX))


def _seed_runs(db_session: Any, *, count: int = 400) -> None:
    """Enough rows that the planner has a REASON to prefer the selective partial index.

    On an empty table every index scan costs about the same, so the assertion below
    reduced to "some index was chosen" — and it broke the moment `runs` gained a second
    index (#1245's `ix_runs_created_id`), which the planner then picked while scanning
    the whole relation. The property under test is that the marker lookup can use this
    index, and that is only observable when using it is the cheaper answer.
    """
    user = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@x.io")
    db_session.add(user)
    db_session.flush()
    connection = Connection(
        name=f"sf-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={"account": "ACCT"},
        secret_ref="ref",
        created_by=user.id,
    )
    db_session.add(connection)
    db_session.flush()
    suite = Suite(name=f"s-{uuid.uuid4().hex[:8]}", connection_id=connection.id, created_by=user.id)
    db_session.add(suite)
    db_session.flush()
    db_session.add_all(
        Run(
            suite_id=suite.id,
            status="succeeded",
            triggered_by=f"airflow:dag_{i}:run_{i}",
        )
        for i in range(count)
    )
    db_session.flush()
    db_session.execute(text("ANALYZE runs"))


def test_marker_to_runs_lookup_uses_the_index(db_session: Any) -> None:
    """A partial index is only usable when the planner can PROVE the query predicate implies
    the index predicate. `triggered_by IN (:literals)` implies `triggered_by IS NOT NULL`
    (the `IN` operator is strict) — but it would NOT imply the LIKE-list predicate
    `uq_runs_suite_triggered_by` carries, which is why this index uses a different one.

    `enable_seqscan = off` removes the empty-table cost preference; it cannot make an
    unprovable partial index eligible.
    """
    _seed_runs(db_session)
    connection = db_session.connection()
    connection.execute(text("SET LOCAL enable_seqscan = off"))
    statement = markers.runs_for_markers_statement(
        {"airflow:nightly_etl:manual__1", "adf:pl_load:abc-123"}
    )
    plan = _plan(connection, statement)
    assert _INDEX in plan, f"the marker -> runs lookup did not use {_INDEX}:\n{plan}"


def test_pipeline_run_marker_lookup_still_uses_the_expression_index(db_session: Any) -> None:
    """The other half of the same correlation, and the half #1715 was filed about: it was
    already indexed by #1814, so this PR adds nothing there. Asserted rather than assumed,
    because the expression index only applies while `_reconstructed_marker` keeps emitting
    `||` — `func.concat(...)`, which this code used before #1728, compiles to a different
    expression the planner will not match.
    """
    connection = db_session.connection()
    connection.execute(text("SET LOCAL enable_seqscan = off"))
    marker = "airflow:nightly_etl:manual__1"
    statement = markers.pipeline_runs_for_marker_statement(marker)
    plan = _plan(connection, statement)
    assert _PIPELINE_INDEX in plan, f"the marker lookup did not use {_PIPELINE_INDEX}:\n{plan}"
