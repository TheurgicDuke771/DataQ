"""Up/down test for `7a1c5d9e2b44_widen_trigger_providers_for_suite` (ADR 0051, #244)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

from backend.tests.support.migrations import run_migration

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


@pytest.mark.usefixtures("db_session")
def test_downgrade_narrows_and_upgrade_widens_both_value_sets(db_session: Any) -> None:
    db = db_session
    assert "'suite'" in _provider_check(db)
    assert "suite:%" in _dedup_predicate(db)

    run_migration(db, _FILE, "downgrade")
    assert "'suite'" not in _provider_check(db)
    assert "suite:%" not in _dedup_predicate(db)
    # The orchestration set is untouched either way.
    assert "'dbt'" in _provider_check(db)

    run_migration(db, _FILE, "upgrade")
    assert "'suite'" in _provider_check(db)
    assert "suite:%" in _dedup_predicate(db)
