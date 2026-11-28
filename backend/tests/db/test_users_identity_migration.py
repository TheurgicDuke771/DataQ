"""Up/down test for `7d25617cfaf0_users_nullable_aad_unique_lower_email` (#735 step 1)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import inspect, text

from backend.tests.support.migrations import load_migration, migration_ops

_MIGRATION = "7d25617cfaf0_users_nullable_aad_unique_lower_email.py"

_INDEX = "uq_users_email_lower"


def _aad_nullable(connection: Any) -> bool:
    cols = {c["name"]: c for c in inspect(connection).get_columns("users")}
    return bool(cols["aad_object_id"]["nullable"])


def _has_lower_email_index(connection: Any) -> bool:
    matches = connection.execute(
        text(
            "SELECT count(*) FROM pg_indexes WHERE tablename = 'users' "
            "AND indexname = :name AND indexdef ILIKE '%lower(%' "
            "AND indexdef ILIKE 'CREATE UNIQUE INDEX%'"
        ),
        {"name": _INDEX},
    ).scalar_one()
    return bool(matches > 0)


def test_revision_chain() -> None:
    module = load_migration(_MIGRATION)
    assert module.revision == "7d25617cfaf0"
    assert module.down_revision == "70a054d4c469"  # the head this was cut from


def test_docstring_carries_the_mandatory_duplicate_email_audit() -> None:
    """The pre-deploy audit lives in the migration, where whoever deploys it looks."""
    doc = load_migration(_MIGRATION).__doc__ or ""
    assert "GROUP BY 1" in doc and "HAVING count(*) > 1" in doc
    assert "lower(email)" in doc
    assert "0032" in doc  # ADR pointer (decision 6)


def test_up_down_up(db_session: Any) -> None:
    """down (restore NOT NULL, drop index) → up → asserted at each step."""
    module = load_migration(_MIGRATION)
    connection = db_session.connection()
    with migration_ops(connection):
        assert _aad_nullable(connection)  # baseline from create_all
        assert _has_lower_email_index(connection)
        module.downgrade()
        assert not _aad_nullable(connection)
        assert not _has_lower_email_index(connection)
        module.upgrade()
        assert _aad_nullable(connection)
        assert _has_lower_email_index(connection)


def test_upgrade_aborts_when_duplicate_case_insensitive_emails_exist(db_session: Any) -> None:
    """The documented failure mode: skipping the audit fails the migrate job loudly."""
    from sqlalchemy.exc import IntegrityError

    from backend.app.db.models import User

    module = load_migration(_MIGRATION)
    connection = db_session.connection()
    with migration_ops(connection):
        module.downgrade()  # back to the pre-#735 schema, where the duplicates are legal
        oid = uuid.uuid4().hex[:8]
        db_session.add_all(
            [
                User(aad_object_id=f"dup-a-{oid}", email="Audit@Example.com"),
                User(aad_object_id=f"dup-b-{oid}", email="audit@example.com"),
            ]
        )
        db_session.flush()

        with pytest.raises(IntegrityError):
            module.upgrade()


def test_downgrade_refuses_instead_of_deleting_otp_users(db_session: Any) -> None:
    """A NULL-aad user must stop the downgrade, not be silently destroyed."""
    from backend.app.db.models import User

    db_session.add(User(aad_object_id=None, email=f"otp-{uuid.uuid4().hex[:8]}@example.com"))
    db_session.flush()

    module = load_migration(_MIGRATION)
    connection = db_session.connection()
    with migration_ops(connection):
        with pytest.raises(RuntimeError, match="NULL aad_object_id"):
            module.downgrade()
        # …and the refusal is a clean no-op: nothing was dropped or re-tightened.
        assert _aad_nullable(connection)
        assert _has_lower_email_index(connection)


def test_downgrade_succeeds_once_no_null_aad_users_remain(db_session: Any) -> None:
    """The other half of the rollback plan: with the OTP rows resolved, down works."""
    module = load_migration(_MIGRATION)
    connection = db_session.connection()
    with migration_ops(connection):
        assert (
            connection.execute(
                text("SELECT count(*) FROM users WHERE aad_object_id IS NULL")
            ).scalar_one()
            == 0
        )
        module.downgrade()
        assert not _aad_nullable(connection)
        module.upgrade()
