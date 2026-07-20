"""Up/down test for `a7b8c9d0e1f2_backfill_check_dimensions`.

Binds the migration module's own `upgrade()`/`downgrade()` to a live connection
and asserts the effect on real rows — the data movement is the whole point of
this migration, so a structural check would prove nothing.

All DDL/DML runs inside the `db_session`'s rolled-back transaction, so nothing
persists. Skips without TEST_DATABASE_URL (needs real Postgres).
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from typing import Any

from alembic.migration import MigrationContext
from alembic.operations import Operations

from backend.app.db.models import Check, Connection, Suite, User

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "a7b8c9d0e1f2_backfill_check_dimensions.py"
)


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("_backfill_dimensions_migration", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed(db: Any, specs: list[tuple[str, str, str, str | None]]) -> uuid.UUID:
    """`(name, kind, expectation_type, dimension)` checks on a fresh suite."""
    owner = User(aad_object_id=uuid.uuid4().hex, email=f"{uuid.uuid4().hex[:8]}@ex.com")
    db.add(owner)
    db.flush()
    conn = Connection(
        name=f"c-{uuid.uuid4().hex[:8]}",
        type="snowflake",
        env="dev",
        config={},
        created_by=owner.id,
    )
    db.add(conn)
    db.flush()
    suite = Suite(name=f"s-{uuid.uuid4().hex[:6]}", connection_id=conn.id, created_by=owner.id)
    db.add(suite)
    db.flush()
    for name, kind, etype, dimension in specs:
        db.add(
            Check(
                suite_id=suite.id,
                name=name,
                kind=kind,
                expectation_type=etype,
                config={},
                dimension=dimension,
            )
        )
    db.flush()
    return suite.id


def _dimensions(db: Any, suite_id: uuid.UUID) -> dict[str, str | None]:
    return {c.name: c.dimension for c in db.query(Check).filter(Check.suite_id == suite_id).all()}


def _run(db: Any, direction: str) -> None:
    migration = _load_migration()
    connection = db.connection()
    ctx = MigrationContext.configure(connection)
    with Operations.context(ctx):
        getattr(migration, direction)()
    db.expire_all()  # the UPDATE bypassed the ORM identity map


def test_upgrade_fills_only_derivable_nulls(db_session: Any) -> None:
    suite_id = _seed(
        db_session,
        [
            ("nulls", "expectation", "expect_column_values_to_not_be_null", None),
            ("unique", "expectation", "expect_column_values_to_be_unique", None),
            ("fresh", "freshness", "monitor:freshness", None),
            # Custom SQL is an arbitrary predicate: ADR 0038 §3 keeps NULL a real
            # state rather than a gap to fill with a guess.
            ("custom", "expectation", "unexpected_rows_expectation", None),
        ],
    )
    _run(db_session, "upgrade")

    assert _dimensions(db_session, suite_id) == {
        "nulls": "completeness",
        "unique": "uniqueness",
        "fresh": "timeliness",
        "custom": None,
    }


def test_upgrade_never_overwrites_an_existing_value(db_session: Any) -> None:
    """Only NULLs. The amendment's argument rests on there being no human-set
    dimension at backfill time — but the SQL must not depend on that being true,
    or a re-run (or an out-of-order deploy) would silently reclassify."""
    suite_id = _seed(
        db_session,
        [("nulls", "expectation", "expect_column_values_to_not_be_null", "accuracy")],
    )
    _run(db_session, "upgrade")
    assert _dimensions(db_session, suite_id) == {"nulls": "accuracy"}


def test_upgrade_is_idempotent(db_session: Any) -> None:
    suite_id = _seed(
        db_session, [("nulls", "expectation", "expect_column_values_to_not_be_null", None)]
    )
    _run(db_session, "upgrade")
    _run(db_session, "upgrade")
    assert _dimensions(db_session, suite_id) == {"nulls": "completeness"}


def test_downgrade_clears_derived_values_but_keeps_a_user_override(db_session: Any) -> None:
    """The case the down path exists to protect. A user who reclassifies a check
    after the upgrade must not lose that on a rollback — so the downgrade clears
    only values still equal to what this migration would have written."""
    suite_id = _seed(
        db_session,
        [
            ("derived", "expectation", "expect_column_values_to_not_be_null", None),
            ("overridden", "expectation", "expect_column_values_to_be_unique", None),
        ],
    )
    _run(db_session, "upgrade")
    db_session.query(Check).filter(Check.name == "overridden").update({"dimension": "accuracy"})
    db_session.flush()

    _run(db_session, "downgrade")

    assert _dimensions(db_session, suite_id) == {"derived": None, "overridden": "accuracy"}


def test_the_frozen_map_matched_the_live_derivation_when_written(db_session: Any) -> None:
    """The migration inlines its map on purpose — it must describe its own point
    in history, not follow a live module. This pins that the two AGREED on
    2026-07-19.

    If the live map later changes, this test fails: that is the prompt to decide
    whether old rows should be re-derived, not a signal to sync the migration.
    Update the expectation here and record the decision; never edit the migration.
    """
    from backend.app.services.check_dimension import derive_dimension

    migration = _load_migration()
    for etype, dimension in migration._BY_EXPECTATION_TYPE.items():
        assert derive_dimension(expectation_type=etype, kind="expectation") == dimension
    for kind, dimension in migration._BY_KIND.items():
        assert derive_dimension(expectation_type=f"monitor:{kind}", kind=kind) == dimension
