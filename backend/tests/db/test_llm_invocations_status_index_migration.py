"""Up/down test for `4a5f1e4d5daa_add_llm_invocations_status_open_index` (#1717)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from backend.app.db.models import LLM_INVOCATION_OPEN_STATUSES, LLM_INVOCATION_STATUSES

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "4a5f1e4d5daa_add_llm_invocations_status_open_index.py"
)
_INDEX = "ix_llm_invocations_status_open"


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("_llm_status_index_migration", _MIGRATION_PATH)
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


def _assert_partial_on_open_statuses(indexdef: str | None) -> None:
    """Postgres rewrites the predicate on read-back (`= ANY (ARRAY[...])`), so assert the parts
    that survive the rewrite: keyed on `status`, partial, and covering exactly the two open states.
    """
    assert indexdef is not None, f"{_INDEX} is missing"
    head, sep, predicate = indexdef.partition(" WHERE ")
    assert sep, f"{_INDEX} is not a partial index: {indexdef}"
    assert "(status)" in head, indexdef
    for status in LLM_INVOCATION_STATUSES:
        covered = f"'{status}'" in predicate
        assert covered == (status in LLM_INVOCATION_OPEN_STATUSES), (status, predicate)


def test_revision_chain() -> None:
    module = _load_migration()
    assert module.revision == "4a5f1e4d5daa"
    assert module.down_revision == "cadc40254699"


def test_create_all_builds_the_partial_index(db_session: Any) -> None:
    """The model side: `Base.metadata.create_all` (the test-DB path) declares the same index."""
    _assert_partial_on_open_statuses(_indexdef(db_session.connection()))


def test_up_down_up(db_session: Any) -> None:
    """down (drop) -> up (recreate) -> down, against live DDL from the `create_all` baseline."""
    module = _load_migration()
    connection = db_session.connection()
    ctx = MigrationContext.configure(connection)
    with Operations.context(ctx):
        _assert_partial_on_open_statuses(_indexdef(connection))
        module.downgrade()
        assert _indexdef(connection) is None
        module.upgrade()
        _assert_partial_on_open_statuses(_indexdef(connection))
        module.downgrade()
        assert _indexdef(connection) is None
        module.upgrade()


def test_reaper_equality_predicates_can_use_the_index(db_session: Any) -> None:
    """The reaper filters `status = 'pending' AND created_at < …` and
    `status = 'running' AND started_at < …` separately (`llm_service.reap_stuck_invocations`),
    never with the IN-list the index is declared on. A partial index is only usable when the
    planner can PROVE the query predicate implies the index predicate — so assert that proof, on
    the reaper's real query shapes, not just the index's existence. `enable_seqscan = off` removes
    the empty-table cost preference; it cannot make an unprovable partial index eligible.
    """
    connection = db_session.connection()
    connection.execute(text("SET LOCAL enable_seqscan = off"))
    for status, age_column in (("pending", "created_at"), ("running", "started_at")):
        plan = "\n".join(
            row[0]
            for row in connection.execute(
                text(
                    "EXPLAIN SELECT id FROM llm_invocations "
                    f"WHERE status = :s AND {age_column} < now()"
                ),
                {"s": status},
            )
        )
        assert _INDEX in plan, f"status = {status!r} did not use {_INDEX}:\n{plan}"
