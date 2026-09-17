"""Up/down + planner test for `3d7c1a9fb204_add_llm_invocations_rca_incident_index` (#1743)."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import text

from backend.app.services import llm_rca

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "3d7c1a9fb204_add_llm_invocations_rca_incident_index.py"
)
_INDEX = "ix_llm_invocations_rca_incident"


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("_llm_rca_index_migration", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _indexdef(connection: Any) -> str | None:
    indexdef: str | None = connection.execute(
        text(
            "SELECT indexdef FROM pg_indexes WHERE tablename = 'llm_invocations' AND indexname = :n"
        ),
        {"n": _INDEX},
    ).scalar_one_or_none()
    return indexdef


def _assert_shape(indexdef: str | None) -> None:
    assert indexdef is not None, f"{_INDEX} is missing"
    head, sep, predicate = indexdef.partition(" WHERE ")
    assert sep, f"{_INDEX} is not a partial index: {indexdef}"
    assert "request ->> 'incident_id'" in head, indexdef
    assert "created_at DESC" in head and "id DESC" in head, indexdef
    assert "rca_narrative" in predicate and "succeeded" in predicate, indexdef


def test_revision_chain() -> None:
    module = _load_migration()
    assert module.revision == "3d7c1a9fb204"
    assert module.down_revision == "2017e2c7ee11"


def test_create_all_builds_the_partial_index(db_session: Any) -> None:
    """The model side: `Base.metadata.create_all` (the test-DB path) declares the same index."""
    _assert_shape(_indexdef(db_session.connection()))


def test_up_down_up(db_session: Any) -> None:
    """down (drop) -> up (recreate) -> down, against live DDL from the `create_all` baseline.

    Runs the migration module's OWN statements, so a wrong index name in `downgrade()` — the
    plausible copy/paste slip from the template migration — goes red here instead of staying
    green against a hand-written copy. Only `CONCURRENTLY` is stripped: it cannot run inside
    this fixture's open transaction, and the real concurrent path is covered by
    `alembic upgrade head` against a scratch database (see the PR).
    """
    module = _load_migration()
    create_sql = module._CREATE_SQL.replace("CONCURRENTLY ", "")
    drop_sql = module._DROP_SQL.replace("CONCURRENTLY ", "")
    assert _INDEX in drop_sql, f"downgrade() drops the wrong index: {drop_sql}"
    connection = db_session.connection()
    for _ in range(2):
        _assert_shape(_indexdef(connection))
        connection.execute(text(drop_sql))
        assert _indexdef(connection) is None
        connection.execute(text(create_sql))
    _assert_shape(_indexdef(connection))


def test_narrative_lookup_uses_the_index(db_session: Any) -> None:
    """The point of the index: the ORM query `latest_narrative_invocation` actually emits must
    reach it. A partial index is only usable when the planner can PROVE the query predicate
    implies the index predicate, and an expression index only when the expression matches
    TEXTUALLY — so assert the plan, not the index's existence.

    `enable_seqscan = off` removes the empty-table cost preference; it cannot make an
    unprovable partial index or a non-matching expression eligible.

    `literal_binds` is the real wire form only because the pinned driver interpolates
    client-side — `test_the_index_depends_on_client_side_parameter_binding` holds that
    assumption to account.
    """
    connection = db_session.connection()
    connection.execute(text("SET LOCAL enable_seqscan = off"))
    statement = llm_rca.narrative_lookup_statement(uuid.uuid4())
    compiled = statement.compile(bind=connection.engine, compile_kwargs={"literal_binds": True})
    plan = "\n".join(row[0] for row in connection.execute(text(f"EXPLAIN {compiled}")))
    assert _INDEX in plan, f"the narrative lookup did not use {_INDEX}:\n{plan}"


def test_the_index_depends_on_client_side_parameter_binding(db_session: Any) -> None:
    """A partial expression index needs CONSTANTS: the planner must prove `kind`/`status`
    imply the index predicate, and `request ->> 'incident_id'` must match the indexed
    expression textually. Under server-side binding those are all parameters, nothing is
    provable, and the query silently falls back to a sequential scan — with the suite green,
    since a wrong plan is still a correct answer.

    `EXPLAIN (GENERIC_PLAN)` (PG16+) plans exactly that case, so assert BOTH halves: the
    generic plan does not reach the index, and the driver actually in use is one that
    interpolates client-side. A psycopg3 swap then turns this from a silent perf regression
    into a failing test (#1979).
    """
    connection = db_session.connection()
    assert connection.dialect.driver == "psycopg2", (
        "the RCA index only applies under client-side parameter binding; "
        f"driver is {connection.dialect.driver!r} — see this test's docstring"
    )
    connection.execute(text("SET LOCAL enable_seqscan = off"))
    generic = "\n".join(
        row[0]
        for row in connection.execute(
            text(
                "EXPLAIN (GENERIC_PLAN) SELECT id FROM llm_invocations "
                "WHERE kind = $1 AND status = $2 AND (request ->> $3) = $4 "
                "ORDER BY created_at DESC, id DESC LIMIT $5"
            )
        )
    )
    assert _INDEX not in generic, (
        "server-side binding now reaches the index — the client-side-binding caveat "
        f"on this index can be dropped:\n{generic}"
    )
