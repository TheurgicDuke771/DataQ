"""Up/down test for the `c4e5a6b7d8f9_add_incidents` migration.

Binds the migration module's own `upgrade()` / `downgrade()` to a live connection
and asserts the `incidents` table (with its partial unique index) and the
`suite_notifications.auto_resolve_incidents` column appear and disappear —
exercising the real DDL. All runs inside the rolled-back `db_session` transaction.

Skips without TEST_DATABASE_URL."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "c4e5a6b7d8f9_add_incidents.py"
)


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("_incidents_migration", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _has_incidents(connection: Any) -> bool:
    return bool(inspect(connection).has_table("incidents"))


def _has_auto_resolve(connection: Any) -> bool:
    cols = {c["name"] for c in inspect(connection).get_columns("suite_notifications")}
    return "auto_resolve_incidents" in cols


def _has_active_index(connection: Any) -> bool:
    if not _has_incidents(connection):
        return False
    names = {ix["name"] for ix in inspect(connection).get_indexes("incidents")}
    return "uq_incidents_active_asset_check" in names


def test_revision_chain() -> None:
    module = _load_migration()
    assert module.revision == "c4e5a6b7d8f9"
    assert module.down_revision == "f0a1b2c3d4e5"


def test_up_down_up(db_session: Any) -> None:
    """down (drop) → up (create) → down (drop). `create_all` (the fixture) already
    made the table + column, so the sequence starts by dropping them."""
    module = _load_migration()
    connection = db_session.connection()
    ctx = MigrationContext.configure(connection)
    with Operations.context(ctx):
        assert _has_incidents(connection)  # baseline from create_all
        assert _has_active_index(connection)
        assert _has_auto_resolve(connection)

        module.downgrade()
        assert not _has_incidents(connection)
        assert not _has_auto_resolve(connection)

        module.upgrade()
        assert _has_incidents(connection)
        assert _has_active_index(connection)
        assert _has_auto_resolve(connection)

        module.downgrade()
        assert not _has_incidents(connection)
    db_session.rollback()  # keep the create_all schema intact for other tests
