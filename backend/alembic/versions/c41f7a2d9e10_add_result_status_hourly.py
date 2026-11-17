"""`result_status_hourly` — result counts per suite, hour and status (#2010)

The dashboard's result-status histogram aggregates every result row in two windows on each
load, and no index removes that: the cost is the join and the count. This adds the table a
later change fills and reads instead.

- `result_status_hourly (suite_id, hour, status, n)`: counts over succeeded runs, by the UTC
  hour of `results.created_at`. `suite_id` is `ON DELETE CASCADE`, as runs and results are.
  Hourly, not daily: a window starts at a moment, not at midnight, and the part-bucket at each
  edge is counted from `results`. An hour of results is cheap to count; a day is not.
- `runs.results_rolled_up_at`: when a run's results were added to the table. NULL for every
  existing row, which a reader treats as "count this run's results from `results`".
- `ix_runs_results_unrolled`: the succeeded runs not yet added. Partial, so it stays small.
- `ix_results_created_brin`: an hour's results by time, for the two part-hours at a window's
  edges. Block-range, so it is a few pages on any table size.

Additive. No code reads or writes any of it yet; that ships in the next PR, after this one is
deployed. Nothing is backfilled here: a beat task in that PR adds existing runs in batches, and
until it has, those runs are still counted from `results`.

Both indexes are built `CONCURRENTLY`, outside the migration's transaction, so neither table
is locked against writes.

Rollback: `alembic downgrade -1` drops the indexes, the column and the table. Tested up, down
and up again against a scratch Postgres database.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "c41f7a2d9e10"
down_revision: str | None = "a5c086f76d06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES = ("pass", "warn", "fail", "critical", "skip", "error")
#: (name, the rest of its CREATE INDEX statement).
_INDEXES = (
    (
        "ix_runs_results_unrolled",
        "ON runs (id) WHERE status = 'succeeded' AND results_rolled_up_at IS NULL",
    ),
    ("ix_results_created_brin", "ON results USING brin (created_at)"),
)


def _create_sql() -> list[str]:
    return [f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} {rest}" for name, rest in _INDEXES]


def _drop_sql() -> list[str]:
    return [f"DROP INDEX CONCURRENTLY IF EXISTS {name}" for name, _rest in _INDEXES]


def _create_table_and_column() -> None:
    op.create_table(
        "result_status_hourly",
        sa.Column("suite_id", UUID(as_uuid=True), nullable=False),
        sa.Column("hour", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("n", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("suite_id", "hour", "status"),
        sa.ForeignKeyConstraint(["suite_id"], ["suites.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in _STATUSES) + ")", name="status_valid"
        ),
    )
    op.add_column(
        "runs", sa.Column("results_rolled_up_at", sa.DateTime(timezone=True), nullable=True)
    )


def _drop_table_and_column() -> None:
    op.drop_column("runs", "results_rolled_up_at")
    op.drop_table("result_status_hourly")


def upgrade() -> None:
    _create_table_and_column()
    # autocommit_block: Postgres refuses CREATE INDEX CONCURRENTLY inside a transaction block.
    with op.get_context().autocommit_block():
        for drop, create in zip(_drop_sql(), _create_sql(), strict=True):
            # DROP first: a crashed earlier attempt can leave an INVALID index that
            # CREATE ... IF NOT EXISTS would then skip over.
            op.execute(drop)
            op.execute(create)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for drop in _drop_sql():
            op.execute(drop)
    _drop_table_and_column()
