"""Up/down test for `7a1c5d9e2b44_widen_trigger_providers_for_suite` (ADR 0051).

The index rebuild uses CONCURRENTLY, which cannot run inside this fixture's open transaction,
so (as `test_runs_triggered_by_index_migration` does) the module's own statements are run with
CONCURRENTLY stripped; the CHECK half runs through the normal migration harness. The live
`alembic upgrade/downgrade` round-trip is exercised separately against a scratch database.
"""

from __future__ import annotations

from typing import Any

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from backend.app.db.models import Base
from backend.tests.support.migrations import load_migration

_FILE = "7a1c5d9e2b44_widen_trigger_providers_for_suite.py"


def _provider_check(db: Any) -> str:
    return str(
        db.scalar(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_trigger_bindings_provider_valid'"
            )
        )
    )


def _dedup_predicate(db: Any) -> str:
    return str(
        db.scalar(
            text(
                "SELECT pg_get_indexdef(indexrelid) FROM pg_index "
                "WHERE indexrelid = 'uq_runs_suite_triggered_by'::regclass"
            )
        )
    )


def _rebuild_index(db: Any, module: Any, predicate: str) -> None:
    for statement in module._index_statements(predicate):
        db.execute(text(statement.replace("CONCURRENTLY ", "")))


def test_revision_chain() -> None:
    module = load_migration(_FILE)
    assert (module.revision, module.down_revision) == ("7a1c5d9e2b44", "e27cb7be87cd")


def test_downgrade_narrows_and_upgrade_widens_both_value_sets(db_session: Any) -> None:
    db = db_session
    module = load_migration(_FILE)
    assert "'suite'" in _provider_check(db)
    assert "suite:%" in _dedup_predicate(db)

    ctx = MigrationContext.configure(db.connection(), opts={"target_metadata": Base.metadata})
    with Operations.context(ctx):
        module._set_provider_check(module._NO_SUITE)
    _rebuild_index(db, module, module._DEDUP_NO_SUITE)
    assert "'suite'" not in _provider_check(db)
    assert "suite:%" not in _dedup_predicate(db)
    assert "'dbt'" in _provider_check(db)  # the orchestration set is untouched either way

    with Operations.context(ctx):
        module._set_provider_check(module._WITH_SUITE)
    _rebuild_index(db, module, module._DEDUP_WITH_SUITE)
    assert "'suite'" in _provider_check(db)
    assert "suite:%" in _dedup_predicate(db)
