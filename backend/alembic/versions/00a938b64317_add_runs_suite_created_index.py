"""Index runs(suite_id, created_at DESC, id DESC) for the health ranking (#999).

Revision ID: 00a938b64317
Revises: a9b8c7d6e5f4
Create Date: 2026-07-26

`datasource_health` ranks a suite's runs newest-first and keeps the top 20, on
every connections page load, for every connection at once. `runs` had
`ix_runs_suite_id` but nothing on `created_at`, so Postgres sorted a suite's
entire run history to answer a question about 20 rows. Invisible on the demo
dataset; linear in history on a real workspace.

The column order mirrors the window's ORDER BY exactly — `PARTITION BY suite_id
ORDER BY created_at DESC, id DESC` (#998 moved the partition from connection to
suite) — so the ranking can walk the index instead of sorting. `id DESC` is
included because it is the tie-break: runs seeded in one transaction share
Postgres' transaction-scoped `now()`, and without a total order the "newest" row
is arbitrary (the #928 trap).

**CREATE INDEX CONCURRENTLY, so this cannot lock out writers.** `runs` is written
by every execution and by the beat; a plain CREATE INDEX takes a SHARE lock for
its whole duration, blocking every write to the table — which is a self-inflicted
version of the #748 incident. CONCURRENTLY cannot run inside a transaction block,
which is what `op.get_context().autocommit_block()` is for — it steps out of the
enclosing transaction for the statement and back in afterwards. (An earlier draft
set a module-level `transactional_ddl = False`; that is a DIALECT attribute, not a
per-revision alembic hook, and it silently did nothing — the migration still ran
in a transaction and failed. Caught by running it.) That interaction is resolved,
not worked around silently (#999 AC 2).

Consequences of CONCURRENTLY, stated because they are real:

* it is not atomic with the rest of the deploy — a failure leaves an INVALID
  index behind, which Postgres will not use. `IF NOT EXISTS` makes a re-run safe,
  but a leftover invalid index must be dropped by hand:
  `DROP INDEX CONCURRENTLY ix_runs_suite_created;`
* it takes two table scans and is slower in wall-clock than the locking form.
  That is the trade: slower deploy, no write outage.

Backward compatible — an index is invisible to application code. Tested up and
down locally.
"""

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
