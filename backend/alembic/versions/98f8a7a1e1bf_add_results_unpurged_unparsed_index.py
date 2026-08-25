"""Partial index supporting the unparsed_value retention-sweep predicate (#1267)"""

from collections.abc import Sequence

from alembic import op

revision: str = "98f8a7a1e1bf"
down_revision: str | None = "9a9ccf49cc8c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # autocommit_block: Postgres refuses CREATE INDEX CONCURRENTLY inside a transaction block, so
    # step out of alembic's for this statement (mirrors fbf4fe92e295's sibling indexes).
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_results_unpurged_unparsed")
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_results_unpurged_unparsed "
            "ON results (created_at) WHERE observed_value ? 'unparsed_value'"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_results_unpurged_unparsed")
