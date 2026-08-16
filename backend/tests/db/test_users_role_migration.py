"""Up/down test for the `f58b4bff54f7_add_users_role` migration (ADR 0033, #740).

The suite's schema comes from `Base.metadata.create_all`, so nothing else in the
test run ever executes this migration's DDL. That gap is what this file closes,
and it matters more than usual here: the column is NOT NULL with a server
default, and the difference between "NOT NULL with a default" (a metadata-only
ADD that succeeds on a populated table) and "NOT NULL without one" (a constraint
violation on the first existing row) is invisible in the model and fatal in
production. So the up-path is asserted against a table that already HAS a row.

Binds the migration module's own `upgrade()`/`downgrade()` to a live connection
via an Alembic `Operations` context — the real DDL, not the module's structure.
All of it runs inside `db_session`'s rolled-back transaction, so nothing persists.

Skips without TEST_DATABASE_URL (needs real Postgres)."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from typing import Any

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "f58b4bff54f7_add_users_role.py"
)


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("_users_role_migration", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _has_role(connection: Any) -> bool:
    return "role" in {c["name"] for c in inspect(connection).get_columns("users")}


def _check_names(connection: Any) -> set[str]:
    return {c["name"] for c in inspect(connection).get_check_constraints("users")}


def test_revision_chain() -> None:
    """The chain, asserted explicitly — a duplicated or misparented revision id
    has bitten this repo twice, and alembic only notices at deploy time."""
    module = _load_migration()
    assert module.revision == "f58b4bff54f7"
    assert module.down_revision == "bee3e56e1a5d"


def test_up_down_up(db_session: Any) -> None:
    """down (drop) → up (add) → down (drop) against the live `users` table."""
    module = _load_migration()
    connection = db_session.connection()
    ctx = MigrationContext.configure(connection)
    with Operations.context(ctx):
        assert _has_role(connection)  # baseline from create_all
        assert "ck_users_role_valid" in _check_names(connection)

        module.downgrade()
        assert not _has_role(connection)
        assert "ck_users_role_valid" not in _check_names(connection)

        module.upgrade()
        assert _has_role(connection)
        assert "ck_users_role_valid" in _check_names(connection)

        module.downgrade()
        assert not _has_role(connection)
    db_session.rollback()


def test_upgrade_backfills_an_existing_row_to_member(db_session: Any) -> None:
    """The additive claim, proven on a POPULATED table.

    A pre-existing user row must survive the upgrade and land on `member` — the
    tier that matches what it could already do before roles existed. This is the
    assertion that would catch a NOT NULL column shipped without a server
    default: that variant passes `test_up_down_up` (which runs against whatever
    rows happen to exist) and fails here, and would fail in production against
    every real deployment.
    """
    module = _load_migration()
    connection = db_session.connection()
    email = f"legacy-{uuid.uuid4().hex[:8]}@example.com"

    ctx = MigrationContext.configure(connection)
    with Operations.context(ctx):
        module.downgrade()
        # A row that predates the column, inserted while the column is absent.
        connection.execute(
            text(
                "INSERT INTO users (id, email, created_at, updated_at) "
                "VALUES (:i, :e, now(), now())"
            ),
            {"i": uuid.uuid4(), "e": email},
        )
        module.upgrade()
        role = connection.execute(
            text("SELECT role FROM users WHERE email = :e"), {"e": email}
        ).scalar_one()
        assert role == "member"
    db_session.rollback()


def test_the_check_constraint_the_migration_creates_actually_enforces(db_session: Any) -> None:
    """The constraint is asserted by NAME above; this asserts it by BEHAVIOUR.

    A `create_check_constraint` with a malformed predicate still produces a
    correctly-named object, so a name check alone would pass over a constraint
    that rejects nothing.
    """
    module = _load_migration()
    connection = db_session.connection()
    ctx = MigrationContext.configure(connection)
    with Operations.context(ctx):
        module.downgrade()
        module.upgrade()
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO users (id, email, role, created_at, updated_at) "
                    "VALUES (:i, :e, 'superuser', now(), now())"
                ),
                {"i": uuid.uuid4(), "e": f"bad-{uuid.uuid4().hex[:8]}@example.com"},
            )
    db_session.rollback()


def test_migration_roles_match_the_model_vocabulary() -> None:
    """The migration deliberately hard-codes its role literals rather than
    importing `WORKSPACE_ROLES` (a migration must describe the schema at ITS
    revision, and stay correct when a later one widens the set). At THIS
    revision the two must still agree — otherwise the model can write a role the
    database rejects."""
    from backend.app.db.models import WORKSPACE_ROLES

    assert set(_load_migration()._ROLES) == set(WORKSPACE_ROLES)
