"""Index runs(suite_id, created_at DESC, id DESC) for the health ranking (#999)."""

from collections.abc import Sequence

from alembic import op

revision: str = "00a938b64317"
down_revision: str | None = "a9b8c7d6e5f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # autocommit_block: Postgres refuses CREATE INDEX CONCURRENTLY inside a
    # transaction block, so step out of alembic's for this statement.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_runs_suite_created "
            "ON runs (suite_id, created_at DESC, id DESC)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_runs_suite_created")
