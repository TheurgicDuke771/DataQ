"""Partial index supporting the result-retention sweep predicate (#323)

Revision ID: fbf4fe92e295
Revises: 5ffa2405f9e8
Create Date: 2026-08-13

The daily `purge_sample_failures` beat task (`run_service.
purge_expired_sample_failures`) scrubs `sample_failures`/`observed_value` off
results past the retention window with

    UPDATE results SET ... WHERE created_at < cutoff
      AND sample_failures_purged_at IS NULL AND jsonb_typeof(...) NOT IN (...)

`results.__table_args__` only indexed `run_id` and `check_id` — nothing on
`created_at` or `sample_failures_purged_at` — so this filtered a full
sequential scan of an unbounded table (one row per check per run, never
row-deleted — ADR 0012 keeps `metric_value` for trends) every day to find a
small not-yet-purged set.

`ix_results_unpurged_created ON results (created_at) WHERE
sample_failures_purged_at IS NULL` matches the sweep's two most selective
predicates directly, and the partial WHERE keeps the index itself small: once
a row is purged it drops out of the index permanently (`sample_failures_
purged_at` only ever moves from NULL to non-NULL — #323's batching loop
depends on this same monotonic-exclusion property for termination), so the
index only ever covers the sweep's actual working set, not the whole table.

**CREATE INDEX CONCURRENTLY, so this cannot lock out writers.** `results` is
written by every check in every run; a plain CREATE INDEX takes a SHARE lock
for its whole duration, blocking every result insert — the same self-inflicted
#748-shaped incident the `00a938b64317` migration avoided on `runs`.
CONCURRENTLY cannot run inside a transaction block, which is what
`op.get_context().autocommit_block()` steps out for (see `env.py`'s
`transaction_per_migration=True`, added for exactly this).

Consequences of CONCURRENTLY, stated because they are real:

* it is not atomic with the rest of the deploy — a failure leaves an INVALID
  index behind, which Postgres will not use. `IF NOT EXISTS` makes a re-run
  safe, but a leftover invalid index must be dropped by hand:
  `DROP INDEX CONCURRENTLY ix_results_unpurged_created;`
* it takes two table scans and is slower in wall-clock than the locking form.
  That is the trade: slower deploy, no write outage.

Backward compatible — an index is invisible to application code; the
`jsonb_typeof` guards in the sweep's WHERE clause are unchanged. Tested
`alembic upgrade head` and `alembic downgrade -1` locally against real
Postgres.

Rollback plan: `alembic downgrade -1` drops the index (also CONCURRENTLY, so
downgrade doesn't lock either); the sweep still functions without it, just
back to the pre-#323 sequential scan.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "fbf4fe92e295"
down_revision: str | None = "5ffa2405f9e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # autocommit_block: Postgres refuses CREATE INDEX CONCURRENTLY inside a
    # transaction block, so step out of alembic's for this statement.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_results_unpurged_created "
            "ON results (created_at) WHERE sample_failures_purged_at IS NULL"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_results_unpurged_created")
