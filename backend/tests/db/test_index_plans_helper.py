"""`drop_rival_indexes` leaves the planner only the index under test (#2545, #2739)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from backend.tests.support.index_plans import drop_rival_indexes

_INDEXES = text(
    "SELECT indexrelid::regclass::text FROM pg_index WHERE indrelid = 'pipeline_runs'::regclass"
)


def _left(connection: Any) -> set[str]:
    return set(connection.execute(_INDEXES).scalars())


def test_only_non_unique_rivals_go_by_default(db_session: Any) -> None:
    connection = db_session.connection()
    dropped = drop_rival_indexes(connection, "ix_pipeline_runs_marker")

    assert "ix_pipeline_runs_provider_pipeline" in dropped
    assert _left(connection) == {
        "pk_pipeline_runs",
        "uq_pipeline_runs_provider_run",
        "ix_pipeline_runs_marker",
    }


def test_unique_rivals_go_with_their_constraint_and_the_primary_key_stays(
    db_session: Any,
) -> None:
    connection = db_session.connection()
    drop_rival_indexes(connection, "ix_pipeline_runs_marker", unique=True)

    assert _left(connection) == {"pk_pipeline_runs", "ix_pipeline_runs_marker"}
    constraints = set(
        connection.execute(
            text("SELECT conname FROM pg_constraint WHERE conrelid = 'pipeline_runs'::regclass")
        ).scalars()
    )
    assert "uq_pipeline_runs_provider_run" not in constraints
