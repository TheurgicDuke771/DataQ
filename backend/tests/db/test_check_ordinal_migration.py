"""Up/down test for `49bd96efd9fc_check_ordinal` (#1334)."""

from __future__ import annotations

import importlib.util
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from backend.app.db.models import Connection, Suite, User

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "49bd96efd9fc_check_ordinal.py"
)
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _run(db: Any, direction: str) -> None:
    spec = importlib.util.spec_from_file_location("_check_ordinal_migration", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = MigrationContext.configure(db.connection())
    with Operations.context(ctx):
        getattr(module, direction)()
    db.expire_all()


def _suite(db: Any) -> uuid.UUID:
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
    return suite.id


def _check(
    db: Any, suite_id: uuid.UUID, name: str, minutes: int, check_id: str | None = None
) -> None:
    # Raw SQL: after the downgrade the ORM model has a column the table does not.
    db.execute(
        text(
            "INSERT INTO checks (id, suite_id, name, expectation_type, config, created_at) "
            "VALUES (:id, :suite, :name, 'e', '{}'::jsonb, :at)"
        ),
        {
            "id": check_id or str(uuid.uuid4()),
            "suite": suite_id,
            "name": name,
            "at": T0 + timedelta(minutes=minutes),
        },
    )


def _ordinals(db: Any, suite_id: uuid.UUID) -> dict[str, int | None]:
    rows = db.execute(
        text("SELECT name, ordinal FROM checks WHERE suite_id = :suite"), {"suite": suite_id}
    )
    return dict(rows.tuples().all())


def test_upgrade_numbers_each_suites_checks_in_the_order_they_list_today(
    db_session: Any,
) -> None:
    _run(db_session, "downgrade")  # the fixture schema already has the column
    first, second = _suite(db_session), _suite(db_session)
    _check(db_session, first, "newest", 30)
    _check(db_session, first, "oldest", 10)
    # Same timestamp: the id decides, as it does in every listing today.
    _check(db_session, first, "tie-low-id", 20, "00000000-0000-0000-0000-000000000001")
    _check(db_session, first, "tie-high-id", 20, "00000000-0000-0000-0000-000000000002")
    _check(db_session, second, "only", 5)

    _run(db_session, "upgrade")

    assert _ordinals(db_session, first) == {
        "oldest": 1,
        "tie-low-id": 2,
        "tie-high-id": 3,
        "newest": 4,
    }
    # Numbering restarts per suite.
    assert _ordinals(db_session, second) == {"only": 1}


def test_downgrade_removes_the_column_and_upgrade_puts_it_back(db_session: Any) -> None:
    def has_column() -> bool:
        return bool(
            db_session.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_name = 'checks' AND column_name = 'ordinal'"
                )
            )
        )

    _run(db_session, "downgrade")
    gone = has_column()
    _run(db_session, "upgrade")

    assert (gone, has_column()) == (False, True)
