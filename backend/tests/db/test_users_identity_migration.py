"""Up/down test for `7d25617cfaf0_users_nullable_aad_unique_lower_email` (#735 step 1).

Binds the migration module's own `upgrade()` / `downgrade()` to a live connection
(the sibling `test_lineage_nullable_connection_migration` pattern) and asserts:
`aad_object_id` nullability flips, `uq_users_email_lower` appears/disappears, and
`downgrade()` **refuses** — rather than deleting — when NULL-aad users exist. All
DDL runs inside the rolled-back test transaction.

Skips without TEST_DATABASE_URL (needs real Postgres — an expression index and
`lower()` folding are dialect behaviour, not something SQLite could stand in for).
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from typing import Any

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "7d25617cfaf0_users_nullable_aad_unique_lower_email.py"
)

_INDEX = "uq_users_email_lower"


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("_users_identity_migration", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    module = _load_migration()
    assert module.revision == "7d25617cfaf0"
    assert module.down_revision == "70a054d4c469"  # the head this was cut from


def test_docstring_carries_the_mandatory_duplicate_email_audit() -> None:
    """The pre-deploy audit lives in the migration, where whoever deploys it looks.

    Pinned by a test because it is a deploy-safety instruction, not prose: without
    running it first, a live database holding `Foo@x.com` + `foo@x.com` turns the
    deploy into a failed migrate job.
    """
    doc = _load_migration().__doc__ or ""
    assert "GROUP BY 1" in doc and "HAVING count(*) > 1" in doc
    assert "lower(email)" in doc
    assert "0032" in doc  # ADR pointer (decision 6)


def test_up_down_up(db_session: Any) -> None:
    """down (restore NOT NULL, drop index) → up → asserted at each step.

    `Base.metadata.create_all` already reflects the post-migration model (nullable
    aad + the expression index), so the pass starts with `downgrade()`.
    """
    module = _load_migration()
    connection = db_session.connection()
    ctx = MigrationContext.configure(connection)
    with Operations.context(ctx):
        assert _aad_nullable(connection)  # baseline from create_all
        assert _has_lower_email_index(connection)
        module.downgrade()
        assert not _aad_nullable(connection)
        assert not _has_lower_email_index(connection)
        module.upgrade()
        assert _aad_nullable(connection)
        assert _has_lower_email_index(connection)


def test_upgrade_aborts_when_duplicate_case_insensitive_emails_exist(db_session: Any) -> None:
    """The documented failure mode: skipping the audit fails the migrate job loudly.

    Proves the abort is real (and that it is the INDEX that catches it), not a
    claim in a docstring.
    """
    from sqlalchemy.exc import IntegrityError

    from backend.app.db.models import User

    module = _load_migration()
    connection = db_session.connection()
    ctx = MigrationContext.configure(connection)
    with Operations.context(ctx):
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
    """A NULL-aad user must stop the downgrade, not be silently destroyed.

    Deleting them would CASCADE away their PATs and access shares (and hard-fail
    on `connections/suites/schedules.created_by`, which are NOT NULL with no
    ondelete), so the migration raises instead. The error must name the count and
    stay actionable.
    """
    from backend.app.db.models import User

    db_session.add(User(aad_object_id=None, email=f"otp-{uuid.uuid4().hex[:8]}@example.com"))
    db_session.flush()

    module = _load_migration()
    connection = db_session.connection()
    ctx = MigrationContext.configure(connection)
    with Operations.context(ctx):
        with pytest.raises(RuntimeError, match="NULL aad_object_id"):
            module.downgrade()
        # …and the refusal is a clean no-op: nothing was dropped or re-tightened.
        assert _aad_nullable(connection)
        assert _has_lower_email_index(connection)


def test_downgrade_succeeds_once_no_null_aad_users_remain(db_session: Any) -> None:
    """The other half of the rollback plan: with the OTP rows resolved, down works.

    This is the state during the step-1 deploy itself (#734's upsert code is not
    shipped yet), so the rollback is unconditionally clean there.
    """
    module = _load_migration()
    connection = db_session.connection()
    ctx = MigrationContext.configure(connection)
    with Operations.context(ctx):
        assert (
            connection.execute(
                text("SELECT count(*) FROM users WHERE aad_object_id IS NULL")
            ).scalar_one()
            == 0
        )
        module.downgrade()
        assert not _aad_nullable(connection)
        module.upgrade()
