"""Up/down test for `543b75689c8c_coverage_settings` (#1661)."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from typing import Any

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "543b75689c8c_coverage_settings.py"
)


def _run(db: Any, direction: str) -> None:
    spec = importlib.util.spec_from_file_location("_coverage_settings_migration", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = MigrationContext.configure(db.connection())
    with Operations.context(ctx):
        getattr(module, direction)()
    db.expire_all()


def _present(db: Any) -> tuple[bool, bool]:
    table = db.scalar(
        text(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = 'coverage_settings'"
        )
    )
    column = db.scalar(
        text(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "AND table_name = 'assets' AND column_name = 'auto_coverage_config'"
        )
    )
    return bool(table), bool(column)


def _asset_written_by_old_code(db: Any) -> uuid.UUID:
    # Raw SQL, naming no `auto_coverage_config`: the row the running release writes.
    asset_id = uuid.uuid4()
    db.execute(
        text("INSERT INTO assets (id, namespace, name) VALUES (:id, 'ns', :name)"),
        {"id": asset_id, "name": f"s.t_{asset_id.hex[:8]}"},
    )
    return asset_id


def test_upgrade_leaves_no_setting_and_no_override(db_session: Any) -> None:
    _run(db_session, "downgrade")  # the fixture schema already has both
    existing = _asset_written_by_old_code(db_session)

    _run(db_session, "upgrade")
    written_after = _asset_written_by_old_code(db_session)

    assert db_session.scalar(text("SELECT count(*) FROM coverage_settings")) == 0
    overrides = db_session.scalars(
        text("SELECT auto_coverage_config FROM assets WHERE id IN (:a, :b)"),
        {"a": existing, "b": written_after},
    ).all()
    assert overrides == [None, None]


def test_the_setting_is_one_row_holding_a_json_object(db_session: Any) -> None:
    _run(db_session, "downgrade")
    _run(db_session, "upgrade")
    db_session.execute(text("INSERT INTO coverage_settings (id, config) VALUES (1, '{}'::jsonb)"))

    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.execute(text("UPDATE coverage_settings SET config = '[]'::jsonb"))
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.execute(
            text("INSERT INTO coverage_settings (id, config) VALUES (2, '{}'::jsonb)")
        )


def test_an_override_is_a_json_object_or_absent(db_session: Any) -> None:
    _run(db_session, "downgrade")
    _run(db_session, "upgrade")
    asset = _asset_written_by_old_code(db_session)
    db_session.execute(
        text("UPDATE assets SET auto_coverage_config = '{}'::jsonb WHERE id = :id"), {"id": asset}
    )

    for not_an_object in ("[]", "null", "3"):
        with pytest.raises(IntegrityError), db_session.begin_nested():
            db_session.execute(
                text("UPDATE assets SET auto_coverage_config = CAST(:v AS jsonb) WHERE id = :id"),
                {"v": not_an_object, "id": asset},
            )


def test_downgrade_removes_both_and_upgrade_puts_them_back(db_session: Any) -> None:
    _run(db_session, "downgrade")
    gone = _present(db_session)
    _run(db_session, "upgrade")

    assert gone == (False, False)
    assert _present(db_session) == (True, True)
