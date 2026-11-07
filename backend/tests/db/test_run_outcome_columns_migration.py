"""Up/down test for `a3cda0b90219_add_run_outcome_columns` (#2348)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.tests.support.migrations import load_migration, run_migration

_FILE = "a3cda0b90219_add_run_outcome_columns.py"
_COLUMNS = (
    "recorded_results_total",
    "recorded_checks_total",
    "recorded_checks_passed",
    "recorded_worst_severity",
)


def _run_columns(db: Any) -> set[str]:
    return set(
        db.scalars(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = 'runs'")
        )
    )


def _run_written_by_old_code(db: Any) -> uuid.UUID:
    """A run inserted with only the columns the pre-migration code knows."""
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
    return run_id


def _record(db: Any, run_id: uuid.UUID, **values: Any) -> None:
    sets = ", ".join(f"{name} = :{name}" for name in values)
    db.execute(
        text(f"UPDATE runs SET {sets} WHERE id = :id"), {"id": run_id, **values}
    )  # nosec B608


def test_revision_chain() -> None:
    migration = load_migration(_FILE)
    assert (migration.revision, migration.down_revision) == ("a3cda0b90219", "ea0a2ff2bf6b")


def test_old_code_still_inserts_and_the_outcome_reads_as_not_recorded(db_session: Any) -> None:
    run_migration(db_session, _FILE, "downgrade")  # the fixture schema already has it
    run_id = _run_written_by_old_code(db_session)
    run_migration(db_session, _FILE, "upgrade")

    row = db_session.execute(
        text(f"SELECT {', '.join(_COLUMNS)} FROM runs WHERE id = :id"),  # nosec B608
        {"id": run_id},
    ).one()
    assert tuple(row) == (None, None, None, None)


def test_a_consistent_outcome_is_accepted(db_session: Any) -> None:
    run_migration(db_session, _FILE, "downgrade")
    run_migration(db_session, _FILE, "upgrade")
    run_id = _run_written_by_old_code(db_session)
    _record(
        db_session,
        run_id,
        recorded_results_total=5,
        recorded_checks_total=4,
        recorded_checks_passed=3,
        recorded_worst_severity="fail",
    )
    assert (
        db_session.scalar(
            text("SELECT recorded_worst_severity FROM runs WHERE id = :id"), {"id": run_id}
        )
        == "fail"
    )


@pytest.mark.parametrize(
    "values",
    [
        {"recorded_worst_severity": "pass"},
        {"recorded_worst_severity": "error"},
        {"recorded_results_total": 2, "recorded_checks_total": 3, "recorded_checks_passed": 0},
        {"recorded_results_total": 3, "recorded_checks_total": 2, "recorded_checks_passed": 3},
        {"recorded_results_total": 3, "recorded_checks_total": 2, "recorded_checks_passed": -1},
    ],
)
def test_an_impossible_outcome_is_refused(db_session: Any, values: dict[str, Any]) -> None:
    run_migration(db_session, _FILE, "downgrade")
    run_migration(db_session, _FILE, "upgrade")
    run_id = _run_written_by_old_code(db_session)
    with pytest.raises(IntegrityError), db_session.begin_nested():
        _record(db_session, run_id, **values)


def test_downgrade_removes_the_columns_and_upgrade_puts_them_back(db_session: Any) -> None:
    run_migration(db_session, _FILE, "downgrade")
    after_down = _run_columns(db_session)
    run_migration(db_session, _FILE, "upgrade")

    assert after_down.isdisjoint(_COLUMNS)
    assert set(_COLUMNS) <= _run_columns(db_session)
