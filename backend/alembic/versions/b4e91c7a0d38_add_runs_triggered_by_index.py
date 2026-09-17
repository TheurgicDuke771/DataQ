"""partial index on runs.triggered_by for the marker correlation (#1715)

`orchestration.markers.triggered_runs` resolves each page of the Results page's
pipeline-runs tab back to the DQ runs those pipelines triggered, via
`runs.triggered_by IN (:markers)`. `uq_runs_suite_triggered_by` leads with
`suite_id`, so a marker-only lookup could not use it and the query was a
parallel sequential scan of the whole `runs` table.

`ix_runs_triggered_by ON runs (triggered_by) WHERE triggered_by IS NOT NULL`:
an `IN` of non-null literals provably implies the partial predicate, and the
partial keeps the manual/scheduled/MCP-triggered rows carrying a NULL marker
out of the index.

Additive only. CONCURRENTLY, like the sibling index migrations — `runs` is one
of the largest tables and under continuous write load.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b4e91c7a0d38"
down_revision: str | None = "3d7c1a9fb204"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CREATE_SQL = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_runs_triggered_by "
    "ON runs (triggered_by) WHERE triggered_by IS NOT NULL"
)
_DROP_SQL = "DROP INDEX CONCURRENTLY IF EXISTS ix_runs_triggered_by"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        # DROP first: a crashed earlier attempt can leave an INVALID index that
        # CREATE ... IF NOT EXISTS would then skip over.
        op.execute(_DROP_SQL)
        op.execute(_CREATE_SQL)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(_DROP_SQL)
