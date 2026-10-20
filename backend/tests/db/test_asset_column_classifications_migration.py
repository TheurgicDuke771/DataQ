"""Up/down test for `c7d2e9a41f05_asset_column_classifications` (ADR 0049, #1674)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import CheckConstraint, text
from sqlalchemy.exc import IntegrityError

from backend.app.db.base import Base
from backend.app.db.models import AssetColumnClassification
from backend.tests.support.migrations import load_migration, run_migration, table_exists

_FILE = "c7d2e9a41f05_asset_column_classifications.py"
_TABLE = "asset_column_classifications"


def _column_present(db: Any) -> bool:
    return bool(
        db.scalar(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'assets' "
                "AND column_name = 'detected_classifications_refreshed_at'"
            )
        )
    )


def _cycle(db: Any) -> tuple[bool, bool]:
    """Downgrade (the fixture schema already has both) and upgrade; what was gone in between."""
    run_migration(db, _FILE, "downgrade")
    gone = (not table_exists(db, _TABLE), not _column_present(db))
    run_migration(db, _FILE, "upgrade")
    return gone


def _asset_written_by_old_code(db: Any) -> uuid.UUID:
    # Raw SQL naming no new column: the row the running release writes.
    asset_id = uuid.uuid4()
    db.execute(
        text("INSERT INTO assets (id, namespace, name) VALUES (:id, 'ns', :name)"),
        {"id": asset_id, "name": f"s.t_{asset_id.hex[:8]}"},
    )
    return asset_id


def _insert(db: Any, asset_id: uuid.UUID, **overrides: Any) -> None:
    values: dict[str, Any] = {
        "asset_id": asset_id,
        "column_name": "Email",
        "source": "profiler",
        "verdict": "sensitive",
        "detector": "email",
        "evidence": '{"n": 20, "email_count": 19}',
        "first_detected_at": "2026-10-20T00:00:00Z",
        "last_detected_at": "2026-10-20T00:00:00Z",
    }
    values.update(overrides)
    columns = ", ".join(values)
    params = ", ".join(f"CAST(:{k} AS jsonb)" if k == "evidence" else f":{k}" for k in values)
    db.execute(text(f"INSERT INTO {_TABLE} ({columns}) VALUES ({params})"), values)


def test_downgrade_removes_both_and_upgrade_restores_them_empty(db_session: Any) -> None:
    run_migration(db_session, _FILE, "downgrade")
    existing = _asset_written_by_old_code(db_session)
    run_migration(db_session, _FILE, "upgrade")
    written_after = _asset_written_by_old_code(db_session)

    assert db_session.scalar(text(f"SELECT count(*) FROM {_TABLE}")) == 0
    stamps = db_session.scalars(
        text("SELECT detected_classifications_refreshed_at FROM assets WHERE id IN (:a, :b)"),
        {"a": existing, "b": written_after},
    ).all()
    assert stamps == [None, None]

    assert _cycle(db_session) == (True, True)
    assert table_exists(db_session, _TABLE) and _column_present(db_session)


def test_the_table_holds_only_the_declared_vocabulary(db_session: Any) -> None:
    _cycle(db_session)
    asset = _asset_written_by_old_code(db_session)
    _insert(db_session, asset)  # the happy row

    # Only `sensitive` is ever persisted — a `public` row would be a clearance (ADR 0049 §2).
    for bad in (
        {"column_name": "a", "verdict": "public"},
        {"column_name": "b", "source": "guess"},
        {"column_name": "c", "detector": "phone"},
        {"column_name": "d", "evidence": "[]"},
        {"column_name": "e", "last_detected_at": "2026-10-19T00:00:00Z"},
    ):
        with pytest.raises(IntegrityError), db_session.begin_nested():
            _insert(db_session, asset, **bad)


def test_one_row_per_cell_case_folded(db_session: Any) -> None:
    _cycle(db_session)
    asset = _asset_written_by_old_code(db_session)
    _insert(db_session, asset, column_name="Email")
    with pytest.raises(IntegrityError), db_session.begin_nested():
        _insert(db_session, asset, column_name="EMAIL")
    # The same column on another asset is a different cell.
    _insert(db_session, _asset_written_by_old_code(db_session), column_name="email")


def test_a_classification_goes_with_its_asset(db_session: Any) -> None:
    _cycle(db_session)
    asset = _asset_written_by_old_code(db_session)
    _insert(db_session, asset)
    db_session.execute(text("DELETE FROM assets WHERE id = :id"), {"id": asset})
    assert db_session.scalar(text(f"SELECT count(*) FROM {_TABLE}")) == 0


def test_the_migration_and_the_model_spell_the_vocabularies_the_same() -> None:
    """The migration is a frozen copy and must never import the model, so the two can drift
    with every test green: the parity test compares table shapes, not CHECK bodies."""
    module = load_migration(_FILE)
    model = {
        str(c.name): str(c.sqltext)
        for c in Base.metadata.tables[AssetColumnClassification.__tablename__].constraints
        if isinstance(c, CheckConstraint) and c.name
    }
    prefix = f"ck_{_TABLE}_"  # the metadata naming convention, applied to both sides
    assert model[prefix + "source_valid"] == module._in("source", module._SOURCES)
    assert model[prefix + "verdict_valid"] == module._in("verdict", module._VERDICTS)
    assert model[prefix + "detector_valid"] == module._in("detector", module._DETECTORS)
