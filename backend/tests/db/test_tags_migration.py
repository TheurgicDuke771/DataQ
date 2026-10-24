"""Up/down test for `e27cb7be87cd_add_tags` (#888), against the real test Postgres.

The test schema is built by `create_all`, so the tables already exist: the cycle is downgrade
(tables gone) → upgrade (back, with their cascades) → the cascades proven with raw rows.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text

from backend.app.db.models import Connection, Suite, User
from backend.tests.support.migrations import run_migration, table_exists

_FILE = "e27cb7be87cd_add_tags.py"
_TABLES = ("tags", "suite_tags", "asset_tags", "connection_tags", "check_tags")


def _suite_and_check(db: Any) -> tuple[uuid.UUID, uuid.UUID]:
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
    return suite.id, check_id


def _count(db: Any, table: str) -> int:
    return int(db.scalar(text(f"SELECT count(*) FROM {table}")))


@pytest.mark.usefixtures("db_session")
def test_downgrade_drops_and_upgrade_recreates_with_cascades(db_session: Any) -> None:
    db = db_session
    assert all(table_exists(db, t) for t in _TABLES)

    run_migration(db, _FILE, "downgrade")
    assert not any(table_exists(db, t) for t in _TABLES)

    run_migration(db, _FILE, "upgrade")
    assert all(table_exists(db, t) for t in _TABLES)
    # Both FKs of every join table cascade (`confdeltype = 'c'`).
    cascades = db.execute(
        text(
            "SELECT conname, confdeltype FROM pg_constraint "
            "WHERE conname LIKE 'fk_%tags%' ORDER BY conname"
        )
    ).all()
    assert len(cascades) == 8 and all(kind == "c" for _, kind in cascades)

    suite_id, check_id = _suite_and_check(db)
    tag_id = uuid.uuid4()
    db.execute(text("INSERT INTO tags (id, name) VALUES (:id, 'finance')"), {"id": tag_id})
    db.execute(
        text("INSERT INTO suite_tags (suite_id, tag_id) VALUES (:s, :t)"),
        {"s": suite_id, "t": tag_id},
    )
    db.execute(
        text("INSERT INTO check_tags (check_id, tag_id) VALUES (:c, :t)"),
        {"c": check_id, "t": tag_id},
    )
    # The unique name holds.
    with pytest.raises(Exception, match="uq_tags_name"):
        with db.begin_nested():
            db.execute(
                text("INSERT INTO tags (id, name) VALUES (:id, 'finance')"), {"id": uuid.uuid4()}
            )

    # Entity → assignments: deleting the check removes its row, the suite's stays.
    db.execute(text("DELETE FROM checks WHERE id = :id"), {"id": check_id})
    assert (_count(db, "check_tags"), _count(db, "suite_tags")) == (0, 1)
    # Tag → assignments: deleting the tag detaches it from the suite; the suite survives.
    db.execute(text("DELETE FROM tags WHERE id = :id"), {"id": tag_id})
    assert _count(db, "suite_tags") == 0
    assert db.scalar(text("SELECT count(*) FROM suites WHERE id = :id"), {"id": suite_id}) == 1
