"""Up/down test for `b94674780cb8_add_seasonal_periods` (ADR 0048 phase 4, #2440)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "b94674780cb8_add_seasonal_periods.py"
)


def _run(db: Any, direction: str) -> None:
    spec = importlib.util.spec_from_file_location("_seasonal_periods_migration", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = MigrationContext.configure(db.connection())
    with Operations.context(ctx):
        getattr(module, direction)()
    db.expire_all()


def _has_table(db: Any) -> bool:
    return bool(
        db.scalar(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_name = 'seasonal_periods'"
            )
        )
    )


def _insert(db: Any, **values: Any) -> None:
    columns = ", ".join(values)
    params = ", ".join(f":{k}" for k in values)
    db.execute(text(f"INSERT INTO seasonal_periods ({columns}) VALUES ({params})"), values)


_RECURRING = {
    "name": "Black Friday week",
    "kind": "recurring",
    "start_month": 11,
    "start_day": 24,
    "end_month": 11,
    "end_day": 30,
}
_DATED = {
    "name": "Spring sale 2026",
    "kind": "dated",
    "start_date": "2026-04-03",
    "end_date": "2026-04-12",
    "series": "Spring sale",
}


def test_downgrade_drops_the_table_and_upgrade_restores_it_holding_both_kinds(
    db_session: Any,
) -> None:
    _run(db_session, "downgrade")  # the fixture schema already has the table
    gone = _has_table(db_session)
    _run(db_session, "upgrade")
    _insert(db_session, **_RECURRING)
    _insert(db_session, **_DATED)

    rows = db_session.execute(text("SELECT name, kind FROM seasonal_periods ORDER BY name")).all()

    assert (gone, _has_table(db_session)) == (False, True)
    assert rows == [("Black Friday week", "recurring"), ("Spring sale 2026", "dated")]


@pytest.mark.parametrize(
    "row",
    [
        # A name differing only by case is the same name.
        {**_RECURRING, "name": "black friday WEEK"},
        {**_RECURRING, "name": "n1", "kind": "weekly"},
        {**_RECURRING, "name": "n2", "start_month": 13},
        {**_RECURRING, "name": "n3", "end_day": 0},
        # A recurring entry carries no dates and no series; a dated one no month or day.
        {**_RECURRING, "name": "n4", "start_date": "2026-01-01"},
        {**_RECURRING, "name": "n5", "series": "x"},
        {**_DATED, "name": "n6", "start_month": 1},
        {**_DATED, "name": "n7", "end_date": "2026-04-02"},
        {**_DATED, "name": "n8", "end_date": None},
        # A NULL inside BETWEEN makes the branch NULL, which a CHECK would accept: each column
        # is tested for NULL by name.
        {**_RECURRING, "name": "n9", "end_month": None},
    ],
)
def test_the_table_refuses_a_row_of_the_wrong_shape(db_session: Any, row: dict[str, Any]) -> None:
    _run(db_session, "downgrade")
    _run(db_session, "upgrade")
    _insert(db_session, **_RECURRING)

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            _insert(db_session, **row)
