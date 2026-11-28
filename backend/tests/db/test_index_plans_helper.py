"""`drop_rival_indexes` leaves the planner only the index under test (#2545, #2739)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from backend.tests.support.index_plans import drop_rival_indexes

_INDEXES = text(
    "SELECT indexrelid::regclass::text FROM pg_index WHERE indrelid = CAST(:t AS regclass)"
)


def _indexes(connection: Any, table: str) -> set[str]:
    return set(connection.execute(_INDEXES, {"t": table}).scalars())


def test_only_non_unique_rivals_go_by_default(db_session: Any) -> None:
    connection = db_session.connection()
    dropped = drop_rival_indexes(connection, "ix_pipeline_runs_marker")

    left = _indexes(connection, "pipeline_runs")
    assert "ix_pipeline_runs_provider_pipeline" in dropped
    assert {"pk_pipeline_runs", "uq_pipeline_runs_provider_run", "ix_pipeline_runs_marker"} <= left
    assert "ix_pipeline_runs_provider_pipeline" not in left


def test_unique_rivals_go_and_the_primary_key_stays(db_session: Any) -> None:
    connection = db_session.connection()
    drop_rival_indexes(connection, "ix_pipeline_runs_marker", unique=True)

    assert _indexes(connection, "pipeline_runs") == {"pk_pipeline_runs", "ix_pipeline_runs_marker"}


def test_unique_rivals_a_foreign_key_depends_on_go_with_it(db_session: Any) -> None:
    """A unique constraint and a constraint-less unique index, each referenced by a foreign key
    (one of them from the same table), need CASCADE; the foreign keys go with them."""
    connection = db_session.connection()
    connection.execute(text("""
        CREATE TABLE rival_t (
            id int PRIMARY KEY, code text, alt text, parent text, tag text,
            CONSTRAINT "uq ""rival"" code" UNIQUE (code)
        );
        CREATE UNIQUE INDEX uqix_rival_alt ON rival_t (alt);
        CREATE INDEX ix_rival_tag ON rival_t (tag);
        ALTER TABLE rival_t ADD FOREIGN KEY (parent) REFERENCES rival_t (code);
        CREATE TABLE rival_child (alt text REFERENCES rival_t (alt));
        """))

    dropped = drop_rival_indexes(connection, "ix_rival_tag", unique=True)

    assert sorted(dropped) == sorted(['"uq ""rival"" code"', "uqix_rival_alt"])
    assert _indexes(connection, "rival_t") == {"rival_t_pkey", "ix_rival_tag"}
    foreign_keys = connection.execute(
        text("SELECT count(*) FROM pg_constraint WHERE contype = 'f' AND conname LIKE 'rival_%'")
    ).scalar_one()
    assert foreign_keys == 0
