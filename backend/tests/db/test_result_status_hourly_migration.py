"""Up/down test for `c41f7a2d9e10_add_result_status_hourly` (#2010)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.tests.support.migrations import load_migration, migration_ops, table_exists

_FILE = "c41f7a2d9e10_add_result_status_hourly.py"
_INDEXES = ("ix_runs_results_unrolled", "ix_results_created_at")


def _indexdef(db: Any, name: str) -> str | None:
    found = db.scalar(
        text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname = current_schema() AND indexname = :n"
        ),
        {"n": name},
    )
    return None if found is None else str(found)


def _has_column(db: Any) -> bool:
    return bool(
        db.scalar(
            text(
                "SELECT count(*) FROM information_schema.columns WHERE table_schema = "
                "current_schema() AND table_name = 'runs' AND column_name = 'results_rolled_up_at'"
            )
        )
    )


def _down(db: Any) -> None:
    """`downgrade()`'s own statements, CONCURRENTLY stripped: it cannot run inside this
    fixture's open transaction."""
    module = load_migration(_FILE)
    for name, _rest in module._INDEXES:
        db.execute(text(f"DROP INDEX IF EXISTS {name}"))
    with migration_ops(db.connection()):
        module._drop_table_and_column()
    db.expire_all()


def _up(db: Any) -> None:
    module = load_migration(_FILE)
    with migration_ops(db.connection()):
        module._create_table_and_column()
    for name, rest in module._INDEXES:
        db.execute(text(f"CREATE INDEX IF NOT EXISTS {name} {rest}"))
    db.expire_all()


def _suite_and_run(db: Any) -> tuple[uuid.UUID, uuid.UUID]:
    """A suite and a succeeded run, inserted with only the columns the old code knows."""
    user_id, conn_id, suite_id, run_id = (uuid.uuid4() for _ in range(4))
    db.execute(
        text("INSERT INTO users (id, email) VALUES (:id, :email)"),
        {"id": user_id, "email": f"u-{user_id.hex[:8]}@example.com"},
    )
    db.execute(
        text(
            "INSERT INTO connections (id, name, type, env, config, secret_ref, created_by) "
            "VALUES (:id, :name, 'postgres', 'dev', '{}'::jsonb, 'kv', :uid)"
        ),
        {"id": conn_id, "name": f"c-{conn_id.hex[:8]}", "uid": user_id},
    )
    db.execute(
        text(
            "INSERT INTO suites (id, name, connection_id, created_by) "
            "VALUES (:id, :name, :cid, :uid)"
        ),
        {"id": suite_id, "name": f"s-{suite_id.hex[:8]}", "cid": conn_id, "uid": user_id},
    )
    db.execute(
        text("INSERT INTO runs (id, suite_id, status) VALUES (:id, :suite, 'succeeded')"),
        {"id": run_id, "suite": suite_id},
    )
    return suite_id, run_id


def test_revision_chain() -> None:
    migration = load_migration(_FILE)
    assert (migration.revision, migration.down_revision) == ("c41f7a2d9e10", "a5c086f76d06")


def test_up_down_up(db_session: Any) -> None:
    def present() -> tuple[bool, bool, list[bool]]:
        return (
            table_exists(db_session, "result_status_hourly"),
            _has_column(db_session),
            [_indexdef(db_session, name) is not None for name in _INDEXES],
        )

    assert present() == (True, True, [True, True])  # the fixture schema already has it
    _down(db_session)
    assert present() == (False, False, [False, False])
    _up(db_session)
    assert present() == (True, True, [True, True])
    _down(db_session)
    _up(db_session)
    assert present() == (True, True, [True, True])


def test_the_indexes_are_the_ones_the_model_declares(db_session: Any) -> None:
    _down(db_session)
    _up(db_session)
    unrolled = _indexdef(db_session, "ix_runs_results_unrolled") or ""
    assert "(id)" in unrolled
    assert "status" in unrolled and "succeeded" in unrolled
    assert "results_rolled_up_at IS NULL" in unrolled
    assert "USING btree (created_at)" in (_indexdef(db_session, "ix_results_created_at") or "")


def test_a_run_from_before_the_migration_reads_as_not_rolled_up(db_session: Any) -> None:
    _down(db_session)
    _suite, run_id = _suite_and_run(db_session)
    _up(db_session)
    assert (
        db_session.scalar(
            text("SELECT results_rolled_up_at FROM runs WHERE id = :id"), {"id": run_id}
        )
        is None
    )
    assert db_session.scalar(text("SELECT count(*) FROM result_status_hourly")) == 0


def test_counts_are_keyed_by_suite_hour_and_status_and_die_with_the_suite(db_session: Any) -> None:
    _down(db_session)
    _up(db_session)
    suite_id, _run = _suite_and_run(db_session)
    insert = text(
        "INSERT INTO result_status_hourly (suite_id, hour, status, n) "
        "VALUES (:suite, TIMESTAMPTZ '2026-11-01 05:00+00', :status, :n)"
    )
    db_session.execute(insert, {"suite": suite_id, "status": "pass", "n": 3})
    db_session.execute(insert, {"suite": suite_id, "status": "fail", "n": 1})
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.execute(insert, {"suite": suite_id, "status": "pass", "n": 9})  # same key
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.execute(insert, {"suite": suite_id, "status": "passed", "n": 1})  # not a status

    db_session.execute(text("DELETE FROM suites WHERE id = :id"), {"id": suite_id})
    assert db_session.scalar(text("SELECT count(*) FROM result_status_hourly")) == 0


def test_a_retry_after_a_cancelled_index_build_finds_the_table_and_column_there(
    db_session: Any,
) -> None:
    """`upgrade()` commits the table and the column before the CONCURRENTLY builds, so a retry
    runs those two steps on a schema that already has them."""
    _up(db_session)  # fixture schema: already present
    _up(db_session)  # the retry
    assert table_exists(db_session, "result_status_hourly") and _has_column(db_session)
    _down(db_session)
    _down(db_session)  # a downgrade after a half-finished downgrade
    assert not table_exists(db_session, "result_status_hourly")
    assert not _has_column(db_session)


def test_a_count_cannot_go_negative(db_session: Any) -> None:
    suite_id, _run = _suite_and_run(db_session)
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.execute(
            text(
                "INSERT INTO result_status_hourly (suite_id, hour, status, n) "
                "VALUES (:s, TIMESTAMPTZ '2026-11-01 05:00+00', 'pass', -1)"
            ),
            {"s": suite_id},
        )
