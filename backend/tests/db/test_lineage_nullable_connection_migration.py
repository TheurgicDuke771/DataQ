"""Up/down test for the `1a2b3c4d5e6f_lineage_edges_nullable_connection` migration.

Binds the migration module's own `upgrade()` / `downgrade()` to a live connection
(the sibling `test_asset_description_migration` pattern) and asserts: `connection_id`
nullability flips, the partial unique index appears/disappears, and `downgrade()`
first deletes the NULL-connection rows it would otherwise strand (re-adding NOT NULL
must not fail). All DDL runs inside the rolled-back test transaction.

Skips without TEST_DATABASE_URL (needs real Postgres — partial indexes are dialect-
specific)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "1a2b3c4d5e6f_lineage_edges_nullable_connection.py"
)


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("_lineage_nullable_migration", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _connection_id_nullable(connection: Any) -> bool:
    cols = {c["name"]: c for c in inspect(connection).get_columns("lineage_edges")}
    return bool(cols["connection_id"]["nullable"])


def _has_partial_index(connection: Any) -> bool:
    names = {ix["name"] for ix in inspect(connection).get_indexes("lineage_edges")}
    return any("null" in (n or "").lower() or "pull" in (n or "").lower() for n in names) or (
        connection.execute(
            text(
                "SELECT count(*) FROM pg_indexes WHERE tablename = 'lineage_edges' "
                "AND indexdef ILIKE '%connection_id IS NULL%'"
            )
        ).scalar_one()
        > 0
    )


def test_revision_chain() -> None:
    module = _load_migration()
    assert module.revision == "1a2b3c4d5e6f"
    assert module.down_revision == "c4e5a6b7d8f9"  # chained onto the #775 incidents head


def test_up_down_up(db_session: Any) -> None:
    """down (restore NOT NULL) → up (relax + index) → down, against live DDL.

    `Base.metadata.create_all` already reflects the post-migration model (nullable +
    partial index), so the pass starts with `downgrade()`."""
    module = _load_migration()
    connection = db_session.connection()
    ctx = MigrationContext.configure(connection)
    with Operations.context(ctx):
        assert _connection_id_nullable(connection)  # baseline from create_all
        module.downgrade()
        assert not _connection_id_nullable(connection)
        assert not _has_partial_index(connection)
        module.upgrade()
        assert _connection_id_nullable(connection)
        assert _has_partial_index(connection)


def test_downgrade_deletes_null_connection_rows(db_session: Any) -> None:
    """`downgrade()` must remove pulled (NULL-connection) rows before NOT NULL returns."""
    from backend.app.db.models import Asset, LineageEdge

    up = Asset(namespace="mz://t", name="A")
    down = Asset(namespace="mz://t", name="B")
    db_session.add_all([up, down])
    db_session.flush()
    db_session.add(
        LineageEdge(
            upstream_asset_id=up.id,
            downstream_asset_id=down.id,
            source="marquez",
            connection_id=None,
        )
    )
    db_session.flush()

    module = _load_migration()
    connection = db_session.connection()
    ctx = MigrationContext.configure(connection)
    with Operations.context(ctx):
        module.downgrade()
        remaining = connection.execute(
            text("SELECT count(*) FROM lineage_edges WHERE source = 'marquez'")
        ).scalar_one()
        assert remaining == 0
        assert not _connection_id_nullable(connection)
        module.upgrade()
