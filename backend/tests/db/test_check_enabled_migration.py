"""Up/down test for `da99e8d41845_check_enabled` (#2369)."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from typing import Any

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from backend.app.db.models import Connection, Suite, User

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "da99e8d41845_check_enabled.py"
)
_TABLES = ("checks", "check_versions")


def _run(db: Any, direction: str) -> None:
    spec = importlib.util.spec_from_file_location("_check_enabled_migration", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = MigrationContext.configure(db.connection())
    with Operations.context(ctx):
        getattr(module, direction)()
    db.expire_all()


def _has_column(db: Any, table: str) -> bool:
    return bool(
        db.scalar(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = 'enabled'"
            ),
            {"table": table},
        )
    )


def _check_with_version(db: Any) -> uuid.UUID:
    # Raw SQL, naming no `enabled`: the rows an old release writes, before and after the upgrade.
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex[:8]}@ex.com")
    db.add(owner)
    db.flush()
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}", type="postgres", env="dev", config={}, created_by=owner.id
    )
    db.add(conn)
    db.flush()
    suite = Suite(name=f"s-{uuid.uuid4().hex[:6]}", connection_id=conn.id, created_by=owner.id)
    db.add(suite)
    db.flush()
    check_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO checks (id, suite_id, name, expectation_type, config) "
            "VALUES (:id, :suite, 'c', 'e', '{}'::jsonb)"
        ),
        {"id": check_id, "suite": suite.id},
    )
    db.execute(
        text(
            "INSERT INTO check_versions "
            "(id, check_id, version_no, name, kind, engine, expectation_type, config) "
            "VALUES (:id, :check, 1, 'c', 'expectation', 'gx', 'e', '{}'::jsonb)"
        ),
        {"id": uuid.uuid4(), "check": check_id},
    )
    return check_id


def _enabled(db: Any, check_id: uuid.UUID) -> tuple[bool, bool]:
    check = db.scalar(text("SELECT enabled FROM checks WHERE id = :id"), {"id": check_id})
    version = db.scalar(
        text("SELECT enabled FROM check_versions WHERE check_id = :id"), {"id": check_id}
    )
    return check, version


def test_upgrade_reads_every_existing_check_and_version_as_enabled(db_session: Any) -> None:
    _run(db_session, "downgrade")  # the fixture schema already has the columns
    existing = _check_with_version(db_session)

    _run(db_session, "upgrade")

    assert _enabled(db_session, existing) == (True, True)


def test_a_writer_that_predates_the_column_still_inserts_an_enabled_check(
    db_session: Any,
) -> None:
    """The release running while this migration deploys names no `enabled` in its INSERTs."""
    written_by_old_code = _check_with_version(db_session)

    assert _enabled(db_session, written_by_old_code) == (True, True)


def test_downgrade_removes_both_columns_and_upgrade_puts_them_back(db_session: Any) -> None:
    _run(db_session, "downgrade")
    gone = [_has_column(db_session, t) for t in _TABLES]
    _run(db_session, "upgrade")

    assert gone == [False, False]
    assert [_has_column(db_session, t) for t in _TABLES] == [True, True]
