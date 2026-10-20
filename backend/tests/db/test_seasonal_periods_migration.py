"""Up/down test for `b94674780cb8_add_seasonal_periods` (ADR 0048 phase 4, #2440)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import CheckConstraint, text
from sqlalchemy.exc import IntegrityError

from backend.app.db.base import Base
from backend.app.db.models import SeasonalPeriod
from backend.tests.support.migrations import load_migration, run_migration, table_exists

_FILE = "b94674780cb8_add_seasonal_periods.py"
_TABLE = "seasonal_periods"


def _cycle(db: Any) -> bool:
    """Downgrade (the fixture schema already has the table) and upgrade; whether it was gone
    in between."""
    run_migration(db, _FILE, "downgrade")
    gone = not table_exists(db, _TABLE)
    run_migration(db, _FILE, "upgrade")
    return gone


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
    gone = _cycle(db_session)
    _insert(db_session, **_RECURRING)
    _insert(db_session, **_DATED)
    # Boundaries that must be accepted: 29 February, a wrap over the year end, a one-day
    # dated entry.
    _insert(
        db_session,
        name="Leap day",
        kind="recurring",
        start_month=2,
        start_day=29,
        end_month=2,
        end_day=29,
    )
    _insert(
        db_session,
        name="Year end",
        kind="recurring",
        start_month=12,
        start_day=20,
        end_month=1,
        end_day=5,
    )
    _insert(
        db_session, name="One day", kind="dated", start_date="2026-05-01", end_date="2026-05-01"
    )

    rows = db_session.execute(text("SELECT name FROM seasonal_periods ORDER BY name")).all()

    assert (gone, table_exists(db_session, _TABLE)) == (True, True)
    assert [r[0] for r in rows] == [
        "Black Friday week",
        "Leap day",
        "One day",
        "Spring sale 2026",
        "Year end",
    ]


_SHAPE = "ck_seasonal_periods_shape"
_NAME = "uq_seasonal_periods_name_lower"
_TRIMMED = "ck_seasonal_periods_trimmed"


@pytest.mark.parametrize(
    ("row", "constraint"),
    [
        # A name differing only by case is the same name.
        ({**_RECURRING, "name": "black friday WEEK"}, _NAME),
        ({**_RECURRING, "name": ""}, _TRIMMED),
        ({**_RECURRING, "name": " padded"}, _TRIMMED),
        ({**_DATED, "name": "n0", "series": " "}, _TRIMMED),
        ({**_RECURRING, "name": "n1", "kind": "weekly"}, "ck_seasonal_periods_kind_valid"),
        ({**_RECURRING, "name": "n2", "start_month": 13}, _SHAPE),
        ({**_RECURRING, "name": "n3", "end_day": 0}, _SHAPE),
        # A recurring entry carries no dates and no series; a dated one no month or day.
        ({**_RECURRING, "name": "n4", "start_date": "2026-01-01"}, _SHAPE),
        ({**_RECURRING, "name": "n5", "series": "x"}, _SHAPE),
        ({**_DATED, "name": "n6", "start_month": 1}, _SHAPE),
        ({**_DATED, "name": "n7", "end_date": "2026-04-02"}, _SHAPE),
        ({**_DATED, "name": "n8", "end_date": None}, _SHAPE),
        # A NULL inside BETWEEN makes the branch NULL, which a CHECK would accept: each column
        # is tested for NULL by name.
        ({**_RECURRING, "name": "n9", "end_month": None}, _SHAPE),
        ({**_RECURRING, "name": "n10", "start_month": None}, _SHAPE),
        # A day that exists in no year of its month.
        ({**_RECURRING, "name": "n12", "start_month": 2, "start_day": 30}, _SHAPE),
        ({**_RECURRING, "name": "n13", "end_month": 4, "end_day": 31}, _SHAPE),
    ],
)
def test_the_table_refuses_a_row_of_the_wrong_shape(
    db_session: Any, row: dict[str, Any], constraint: str
) -> None:
    _cycle(db_session)
    _insert(db_session, **_RECURRING)

    with pytest.raises(IntegrityError) as refused:
        with db_session.begin_nested():
            _insert(db_session, **row)

    # The constraint named, not merely some IntegrityError: a neighbouring constraint must
    # not mask a hole in the one under test.
    assert refused.value.orig.diag.constraint_name == constraint  # type: ignore[union-attr]


def test_a_null_kind_is_refused_by_the_column(db_session: Any) -> None:
    _cycle(db_session)

    with pytest.raises(IntegrityError) as refused:
        with db_session.begin_nested():
            _insert(db_session, **{**_RECURRING, "kind": None})

    assert refused.value.orig.diag.column_name == "kind"  # type: ignore[union-attr]


def test_the_migration_and_the_model_spell_the_check_constraints_the_same() -> None:
    """The migration is a frozen copy and must never import the model, so the two texts can
    drift with every test green: the parity test compares vocabularies, not bodies."""
    module = load_migration(_FILE)
    model = {
        str(c.name): str(c.sqltext)
        for c in Base.metadata.tables[SeasonalPeriod.__tablename__].constraints
        if isinstance(c, CheckConstraint) and c.name
    }

    assert model[_SHAPE] == module._SHAPE
    assert model[_TRIMMED] == module._TRIMMED
