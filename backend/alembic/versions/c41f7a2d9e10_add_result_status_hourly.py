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
- `ix_results_created_at`: an hour's results by time, for the two part-hours at a window's
  edges. A plain btree: a block-range index would be a few pages, but it depends on rows being
  stored in time order, which `created_at` (a transaction's start) and reused heap pages do
  not guarantee. 21 MB at one million rows.

Additive. No code reads or writes any of it yet; that ships in the next PR, after this one is
deployed. Nothing is backfilled here: a beat task in that PR adds existing runs in batches, and
until it has, those runs are still counted from `results`.

Both indexes are built `CONCURRENTLY`, outside the migration's transaction, so neither table
is locked against writes. Because that step commits the table and the column first, every step
is re-runnable (`IF NOT EXISTS`, drop-then-create): a build that is cancelled on a large table
is retried by running `alembic upgrade head` again.

Rollback: `alembic downgrade -1` drops the indexes, the column and the table. Tested up, down
and up again against a scratch Postgres database.
"""

from collections.abc import Sequence

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
    ("ix_results_created_at", "ON results (created_at)"),
)


def _create_table_and_column() -> None:
    statuses = ", ".join(f"'{s}'" for s in _STATUSES)
    # IF NOT EXISTS: `upgrade()` commits these before the index builds, so a retry after a
    # cancelled build arrives with both already there.
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS result_status_hourly (
            suite_id UUID NOT NULL,
            hour TIMESTAMP WITH TIME ZONE NOT NULL,
            status VARCHAR(16) NOT NULL,
            n BIGINT NOT NULL,
            CONSTRAINT pk_result_status_hourly PRIMARY KEY (suite_id, hour, status),
            CONSTRAINT fk_result_status_hourly_suite_id_suites
                FOREIGN KEY (suite_id) REFERENCES suites (id) ON DELETE CASCADE,
            CONSTRAINT ck_result_status_hourly_status_valid CHECK (status IN ({statuses})),
            CONSTRAINT ck_result_status_hourly_n_nonnegative CHECK (n >= 0)
        )
        """)
    op.execute("ALTER TABLE runs ADD COLUMN IF NOT EXISTS results_rolled_up_at TIMESTAMPTZ")


def _drop_table_and_column() -> None:
    op.execute("ALTER TABLE runs DROP COLUMN IF EXISTS results_rolled_up_at")
    op.execute("DROP TABLE IF EXISTS result_status_hourly")


def upgrade() -> None:
    _create_table_and_column()
    # autocommit_block: Postgres refuses CREATE INDEX CONCURRENTLY inside a transaction block.
    with op.get_context().autocommit_block():
        for name, rest in _INDEXES:
            # DROP first: a crashed earlier attempt can leave an INVALID index that
            # CREATE ... IF NOT EXISTS would then skip over.
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
            op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} {rest}")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name, _rest in _INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    _drop_table_and_column()
