"""Up/down test for the `f58b4bff54f7_add_users_role` migration (ADR 0033, #740)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from backend.tests.support.migrations import load_migration, migration_ops

_MIGRATION = "f58b4bff54f7_add_users_role.py"


def _has_role(connection: Any) -> bool:
    return "role" in {c["name"] for c in inspect(connection).get_columns("users")}


def _check_names(connection: Any) -> set[str]:
    return {c["name"] for c in inspect(connection).get_check_constraints("users")}


def test_revision_chain() -> None:
    """The chain, asserted explicitly — a duplicated or misparented revision id
    has bitten this repo twice, and alembic only notices at deploy time.
    """
    module = load_migration(_MIGRATION)
    assert module.revision == "f58b4bff54f7"
    assert module.down_revision == "bee3e56e1a5d"


def test_up_down_up(db_session: Any) -> None:
    """down (drop) → up (add) → down (drop) against the live `users` table."""
    module = load_migration(_MIGRATION)
    connection = db_session.connection()
    with migration_ops(connection):
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
    """The additive claim, proven on a POPULATED table."""
    module = load_migration(_MIGRATION)
    connection = db_session.connection()
    email = f"legacy-{uuid.uuid4().hex[:8]}@example.com"

    with migration_ops(connection):
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
    """The constraint is asserted by NAME above; this asserts it by BEHAVIOUR."""
    module = load_migration(_MIGRATION)
    connection = db_session.connection()
    with migration_ops(connection):
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
    """The migration deliberately hard-codes its role literals rather than importing
    `WORKSPACE_ROLES` (a migration must describe the schema at ITS revision, and stay correct
    when a later one widens the set). At THIS revision the two must still agree — otherwise the
    model can write a role the database rejects.
    """
    from backend.app.db.models import WORKSPACE_ROLES

    assert set(load_migration(_MIGRATION)._ROLES) == set(WORKSPACE_ROLES)
