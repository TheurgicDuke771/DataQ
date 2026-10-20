"""Up/down test for `b9ddde2123fe_add_telemetry_settings` (#1707)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.tests.support.migrations import load_migration, run_migration, table_exists

_FILE = "b9ddde2123fe_add_telemetry_settings.py"


def _user_written_by_old_code(db: Any) -> uuid.UUID:
    user_id = uuid.uuid4()
    db.execute(
        text("INSERT INTO users (id, email) VALUES (:id, :email)"),
        {"id": user_id, "email": f"u_{user_id.hex[:8]}@example.com"},
    )
    return user_id


def test_revision_id() -> None:
    assert load_migration(_FILE).revision == "b9ddde2123fe"


def test_upgrade_creates_an_empty_table_with_the_column_defaults(db_session: Any) -> None:
    run_migration(db_session, _FILE, "downgrade")  # the fixture schema already has it
    run_migration(db_session, _FILE, "upgrade")

    assert db_session.scalar(text("SELECT count(*) FROM telemetry_settings")) == 0
    db_session.execute(text("INSERT INTO telemetry_settings (id) VALUES (1)"))
    enabled, updated_at = db_session.execute(
        text("SELECT enabled, updated_at FROM telemetry_settings WHERE id = 1")
    ).one()
    assert enabled is False and updated_at is not None


def test_the_setting_is_one_row(db_session: Any) -> None:
    run_migration(db_session, _FILE, "downgrade")
    run_migration(db_session, _FILE, "upgrade")
    db_session.execute(text("INSERT INTO telemetry_settings (id, enabled) VALUES (1, true)"))

    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.execute(text("INSERT INTO telemetry_settings (id, enabled) VALUES (2, true)"))


def test_the_updater_may_be_deleted(db_session: Any) -> None:
    run_migration(db_session, _FILE, "downgrade")
    run_migration(db_session, _FILE, "upgrade")
    user_id = _user_written_by_old_code(db_session)
    db_session.execute(
        text("INSERT INTO telemetry_settings (id, enabled, updated_by) VALUES (1, true, :u)"),
        {"u": user_id},
    )

    db_session.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})

    assert db_session.scalar(text("SELECT updated_by FROM telemetry_settings WHERE id = 1")) is None


def test_downgrade_removes_it_and_upgrade_puts_it_back(db_session: Any) -> None:
    run_migration(db_session, _FILE, "downgrade")
    gone = table_exists(db_session, "telemetry_settings")
    run_migration(db_session, _FILE, "upgrade")

    assert gone is False
    assert table_exists(db_session, "telemetry_settings") is True
