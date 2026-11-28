"""Up/down test for the `f0a1b2c3d4e5_add_asset_description` migration."""

from __future__ import annotations

from typing import Any

from sqlalchemy import inspect

from backend.tests.support.migrations import load_migration, migration_ops

_MIGRATION = "f0a1b2c3d4e5_add_asset_description.py"


def _has_description(connection: Any) -> bool:
    return "description" in {c["name"] for c in inspect(connection).get_columns("assets")}


def test_revision_chain() -> None:
    module = load_migration(_MIGRATION)
    assert module.revision == "f0a1b2c3d4e5"
    assert module.down_revision == "a1c2e3d4f5b6"


def test_up_down_up(db_session: Any) -> None:
    """down (drop) → up (add) → down (drop) against the live `assets` table."""
    module = load_migration(_MIGRATION)
    connection = db_session.connection()
    with migration_ops(connection):
        assert _has_description(connection)  # baseline from create_all
        module.downgrade()
        assert not _has_description(connection)
        module.upgrade()
        assert _has_description(connection)
        module.downgrade()
        assert not _has_description(connection)
    # Roll back so the create_all schema is intact for other tests on this engine.
    db_session.rollback()
