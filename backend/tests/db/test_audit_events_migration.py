"""Up/down test for `ecda713656ac_add_audit_events` — ADR 0041 phase 1 (#1318).

The suite's schema comes from `Base.metadata.create_all`, so nothing else in the
test run ever executes this migration's DDL. That gap is what this file closes,
and two things here are invisible to `create_all` and therefore only checkable
against the migration itself:

* **`entity_id` must carry NO foreign key.** An FK leaves two options, both
  self-defeating: CASCADE (the audit row dies with the entity — exactly the
  failure this table exists to fix) or RESTRICT (the audit log makes deletion
  impossible). Asserting its *absence* is asserting the load-bearing design
  decision, and it is the kind of thing a later "helpful" autogenerate would add
  back without anyone noticing.
* **The `REVOKE` runs at all.** It is wrapped in a `DO` block that no-ops when the
  role is absent, which is correct for local dev and CI — and which also means a
  broken statement would be *invisible* here. So the block is executed against a
  role that does exist, and the resulting privileges are asserted.

Binds the migration module's own `upgrade()`/`downgrade()` to a live connection
via an Alembic `Operations` context — the real DDL, not the module's structure.
All of it runs inside `db_session`'s rolled-back transaction, so nothing persists.

Skips without TEST_DATABASE_URL (needs real Postgres)."""

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
    has bitten this repo twice, and alembic only notices at deploy time."""
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
    """The no-FK-on-`entity_id` decision, asserted from the migrated schema.

    Stated as a pair with `actor_user_id` deliberately: the table is not
    "FK-free by accident", it makes two *opposite* choices for two columns, and a
    test that only asserted the absence would read as the former.
    """
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
    """Execute the migration's own REVOKE against a role that EXISTS.

    The statement is a `DO` block that no-ops when the role is absent — correct for
    local dev and CI, and precisely why running the migration here proves nothing
    on its own: a broken REVOKE would be indistinguishable from a skipped one. So
    the role is created for the duration of the (rolled-back) transaction, granted
    the privileges the deployed app would hold, and the REVOKE is run against it.

    What this asserts is the honest claim from ADR 0041 §2.7: a guard against
    ACCIDENTAL in-app mutation. It is NOT tamper-resistance — the table's owner can
    grant the privileges straight back — and the test name says "revokes", not
    "prevents tampering", for that reason.
    """
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
    # INSERT and SELECT must SURVIVE — the app has to be able to write events and
    # the admin read surface has to be able to read them. A REVOKE that took those
    # too would break the feature while passing a naive "did it revoke?" check.
    assert {"INSERT", "SELECT"} <= after

    connection.execute(text(f'REVOKE ALL ON audit_events FROM "{role}"'))
    connection.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
