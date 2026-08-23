"""add partial unique index on runs(suite_id, triggered_by) for orchestration markers"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Literal SQL (no interpolation) — the orchestration-marker predicate below is kept in sync with
# ``orchestration_service._ORCH_TRIGGER_PREDICATE`` and the model's ``postgresql_where`` on the


def upgrade() -> None:
    # Collapse any erroneous orchestration-marker duplicates (keep the earliest run per (suite_id,
    # triggered_by)) so the unique index builds cleanly on a live DB.
    op.execute("""
        DELETE FROM runs r
        USING (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY suite_id, triggered_by
                       ORDER BY created_at, id
                   ) AS rn
            FROM runs
            WHERE triggered_by LIKE 'adf:%' OR triggered_by LIKE 'airflow:%'
        ) dup
        WHERE r.id = dup.id AND dup.rn > 1
        """)
    op.execute("""
        CREATE UNIQUE INDEX uq_runs_suite_triggered_by
        ON runs (suite_id, triggered_by)
        WHERE triggered_by LIKE 'adf:%' OR triggered_by LIKE 'airflow:%'
        """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_runs_suite_triggered_by")
