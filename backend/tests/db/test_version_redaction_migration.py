"""Up/down of `878cff0aebbe` (#685): nullable `redacted_at` / `redacted_by` on both version
tables, every existing row unredacted."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, text

from backend.app.db.models import Connection, ConnectionVersion, User
from backend.tests.support.migrations import run_migration

_FILE = "878cff0aebbe_version_redaction_columns.py"
_TABLES = ("connection_versions", "check_versions")
_COLUMNS = ("redacted_at", "redacted_by")


def _columns(db: Any, table: str) -> set[str]:
    rows = db.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = :table"
        ),
        {"table": table},
    )
    return {r[0] for r in rows}


def test_downgrade_drops_the_columns_and_upgrade_restores_them_nullable(db_session: Any) -> None:
    run_migration(db_session, _FILE, "downgrade")
    gone = [set(_COLUMNS) & _columns(db_session, t) for t in _TABLES]
    run_migration(db_session, _FILE, "upgrade")

    assert gone == [set(), set()]
    assert all(set(_COLUMNS) <= _columns(db_session, t) for t in _TABLES)
    fks = db_session.execute(
        text(
            "SELECT conname, confdeltype FROM pg_constraint "
            "WHERE conname LIKE 'fk_%_redacted_by_users' ORDER BY conname"
        )
    ).all()
    assert [(n, d) for n, d in fks] == [
        ("fk_check_versions_redacted_by_users", "n"),
        ("fk_connection_versions_redacted_by_users", "n"),
    ]


def test_a_row_written_before_the_columns_reads_as_unredacted(db_session: Any) -> None:
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex[:8]}@ex.com")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}", type="postgres", env="dev", config={}, created_by=owner.id
    )
    db_session.add(conn)
    db_session.flush()
    # Raw SQL naming neither column: the row an old release writes.
    db_session.execute(
        text(
            "INSERT INTO connection_versions (id, connection_id, version_no, name, type, env, "
            "config, changed_by, created_at) VALUES (:id, :cid, 1, 'c', 'postgres', 'dev', "
            '\'{"host": "db"}\'::jsonb, :uid, now())'
        ),
        {"id": uuid.uuid4(), "cid": conn.id, "uid": owner.id},
    )
    db_session.commit()
    row = db_session.scalars(
        select(ConnectionVersion).where(ConnectionVersion.connection_id == conn.id)
    ).one()
    assert row.redacted is False and row.redacted_by_name is None
    assert row.config == {"host": "db"}
