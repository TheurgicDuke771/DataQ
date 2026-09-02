"""expression index on the reconstructed pipeline-run trigger marker (#1814)

`orchestration.markers` inverts `runs.triggered_by` ("provider:pipeline:run_id") by
comparing it to the same expression over `pipeline_runs`' stored columns, and counts
collisions with a GROUP BY over it — both unindexed until now, and both on the
Results page's poll path. Additive only; CONCURRENTLY like the sibling partial-index
migrations, since `pipeline_runs` grows with every orchestration poll.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "6bcf67868753"
down_revision: str | None = "4a5f1e4d5daa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_pipeline_runs_marker")
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_pipeline_runs_marker "
            "ON pipeline_runs ((provider || ':' || pipeline_or_dag_id || ':' || provider_run_id))"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_pipeline_runs_marker")
