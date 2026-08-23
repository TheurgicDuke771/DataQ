"""Partial indexes supporting the result-retention sweep predicates (#323)"""

from collections.abc import Sequence

from alembic import op

revision: str = "fbf4fe92e295"
down_revision: str | None = "5ffa2405f9e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # autocommit_block: Postgres refuses CREATE INDEX CONCURRENTLY inside a transaction block, so
    # step out of alembic's for these statements.
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_results_unpurged_created")
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_results_unpurged_created "
            "ON results (created_at) WHERE sample_failures_purged_at IS NULL "
            "AND sample_failures IS NOT NULL AND jsonb_typeof(sample_failures) <> 'null'"
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_results_unpurged_observed")
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_results_unpurged_observed "
            "ON results (created_at) "
            "WHERE jsonb_typeof(observed_value -> 'observed_value') = 'array'"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_results_unpurged_observed")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_results_unpurged_created")
