"""Up/down test for the `b2c3d4e5f6a7_add_asset_description` migration.

The migration adds a single nullable `assets.description` column. This binds the
migration module's own `upgrade()` / `downgrade()` to a live connection via an
Alembic `Operations` context and asserts the column appears and disappears —
exercising the real DDL, not just the module's structure. All DDL runs inside the
`db_session`'s rolled-back transaction, so nothing persists.

Skips without TEST_DATABASE_URL (needs real Postgres)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "b2c3d4e5f6a7_add_asset_description.py"
)


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("_asset_description_migration", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _has_description(connection: Any) -> bool:
    return "description" in {c["name"] for c in inspect(connection).get_columns("assets")}


def test_revision_chain() -> None:
    module = _load_migration()
    assert module.revision == "b2c3d4e5f6a7"
    assert module.down_revision == "a1c2e3d4f5b6"


def test_up_down_up(db_session: Any) -> None:
    """down (drop) → up (add) → down (drop) against the live `assets` table.

    `Base.metadata.create_all` (the test fixture) already created the column, so
    the sequence starts by dropping it, then re-adds and re-drops — covering both
    `downgrade()` and `upgrade()` DDL in one pass."""
    module = _load_migration()
    connection = db_session.connection()
    ctx = MigrationContext.configure(connection)
    with Operations.context(ctx):
        assert _has_description(connection)  # baseline from create_all
        module.downgrade()
        assert not _has_description(connection)
        module.upgrade()
        assert _has_description(connection)
        module.downgrade()
        assert not _has_description(connection)
    # Roll back so the create_all schema is intact for other tests on this engine.
    db_session.rollback()
