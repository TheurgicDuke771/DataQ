"""Up/down test for `ea0a2ff2bf6b_settings_singleton_checks` (#2424)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.tests.support.migrations import run_migration

_FILE = "ea0a2ff2bf6b_settings_singleton_checks.py"

# A minimal valid row per table, keyed by id, written in raw SQL as any writer would.
_ROWS = {
    "privacy_settings": "INSERT INTO privacy_settings (id, zero_sample_mode) VALUES ({id}, false)",
    "scoring_settings": (
        "INSERT INTO scoring_settings (id, warn_weight, fail_weight, critical_weight) "
        "VALUES ({id}, 1, 2, 3)"
    ),
    "llm_settings": (
        "INSERT INTO llm_settings (id, provider, model, enabled) "
        "VALUES ({id}, 'openai_compatible', 'm', false)"
    ),
    "audit_chain_state": "INSERT INTO audit_chain_state (id) VALUES ({id})",
}


def _ensure_row_one(db: Any, table: str) -> None:
    # Another test in the session may already hold the singleton row.
    db.execute(text(_ROWS[table].format(id=1) + " ON CONFLICT (id) DO NOTHING"))


@pytest.mark.parametrize("table", sorted(_ROWS))
def test_a_second_row_is_refused(db_session: Any, table: str) -> None:
    _ensure_row_one(db_session, table)
    with pytest.raises(IntegrityError, match=f"ck_{table}_singleton"):
        with db_session.begin_nested():
            db_session.execute(text(_ROWS[table].format(id=2)))


@pytest.mark.parametrize("table", sorted(_ROWS))
def test_downgrade_drops_the_check_and_upgrade_restores_it(db_session: Any, table: str) -> None:
    run_migration(db_session, _FILE, "downgrade")
    with db_session.begin_nested():
        db_session.execute(text(_ROWS[table].format(id=2)))
    db_session.execute(text(f"DELETE FROM {table} WHERE id = 2"))

    run_migration(db_session, _FILE, "upgrade")
    with pytest.raises(IntegrityError, match=f"ck_{table}_singleton"):
        with db_session.begin_nested():
            db_session.execute(text(_ROWS[table].format(id=2)))


def test_upgrade_stops_on_a_stray_row_and_names_the_table(db_session: Any) -> None:
    run_migration(db_session, _FILE, "downgrade")
    db_session.execute(text(_ROWS["scoring_settings"].format(id=7)))

    with pytest.raises(RuntimeError, match="scoring_settings holds 1 row"):
        run_migration(db_session, _FILE, "upgrade")
    assert db_session.scalar(text("SELECT count(*) FROM scoring_settings WHERE id = 7")) == 1
