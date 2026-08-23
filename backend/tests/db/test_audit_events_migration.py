"""Up/down test for `ecda713656ac_add_audit_events` — ADR 0041 phase 1 (#1318)."""

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
    / "ecda713656ac_add_audit_events.py"
)


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("_audit_events_migration", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _has_table(connection: Any) -> bool:
    return "audit_events" in inspect(connection).get_table_names()


def test_revision_chain() -> None:
    """The chain, asserted explicitly — a duplicated or misparented revision id
    has bitten this repo twice, and alembic only notices at deploy time.
    """
    module = _load_migration()
    assert module.revision == "ecda713656ac"
    assert module.down_revision == "f58b4bff54f7"


def test_down_up_down(db_session: Any) -> None:
    """`create_all` already made the table, so the cycle starts with a drop."""
    module = _load_migration()
    connection = db_session.connection()
    ctx = MigrationContext.configure(connection)
    with Operations.context(ctx):
        assert _has_table(connection)  # baseline from create_all

        module.downgrade()
        assert not _has_table(connection)

        module.upgrade()
        assert _has_table(connection)

        module.downgrade()
        assert not _has_table(connection)

        # Leave the schema as the rest of the suite expects it.
        module.upgrade()
        assert _has_table(connection)


def test_entity_id_has_no_foreign_key_and_actor_user_id_sets_null(db_session: Any) -> None:
    """The no-FK-on-`entity_id` decision, asserted from the migrated schema."""
    module = _load_migration()
    connection = db_session.connection()
    ctx = MigrationContext.configure(connection)
    with Operations.context(ctx):
        module.downgrade()
        module.upgrade()

    fks = inspect(connection).get_foreign_keys("audit_events")
    constrained = {col for fk in fks for col in fk["constrained_columns"]}
    assert "entity_id" not in constrained
    actor_fk = next(fk for fk in fks if fk["constrained_columns"] == ["actor_user_id"])
    assert actor_fk["referred_table"] == "users"
    assert actor_fk["options"]["ondelete"].upper() == "SET NULL"


def test_the_revoke_block_actually_revokes(db_session: Any) -> None:
    """Execute the migration's own REVOKE against a role that EXISTS."""
    connection = db_session.connection()
    role = "dataq_audit_revoke_probe"
    connection.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
    connection.execute(text(f'CREATE ROLE "{role}"'))
    connection.execute(text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON audit_events TO "{role}"'))

    def privileges() -> set[str]:
        rows = connection.execute(
            text(
                "SELECT privilege_type FROM information_schema.table_privileges "
                "WHERE table_name = 'audit_events' AND grantee = :role"
            ),
            {"role": role},
        )
        return {r[0] for r in rows}

    assert {"UPDATE", "DELETE"} <= privileges(), "precondition: the probe role starts with both"

    module = _load_migration()
    revoke_sql = module.revoke_statement(role)
    connection.execute(text(revoke_sql))

    after = privileges()
    assert "UPDATE" not in after
    assert "DELETE" not in after
    # INSERT and SELECT must SURVIVE — the app has to be able to write events and the admin read
    # surface has to be able to read them.
    assert {"INSERT", "SELECT"} <= after

    connection.execute(text(f'REVOKE ALL ON audit_events FROM "{role}"'))
    connection.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
