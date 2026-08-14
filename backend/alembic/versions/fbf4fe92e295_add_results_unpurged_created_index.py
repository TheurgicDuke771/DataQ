"""Partial indexes supporting the result-retention sweep predicates (#323)

Revision ID: fbf4fe92e295
Revises: 5ffa2405f9e8
Create Date: 2026-08-13

The daily `purge_sample_failures` beat task (`run_service.
purge_expired_sample_failures`) runs TWO independent UPDATEs — one per
PII-bearing column — each scoped to `created_at < cutoff` plus its own
column-specific guard:

    -- sample_failures half
    UPDATE results SET sample_failures = NULL, sample_failures_purged_at = now()
      WHERE created_at < cutoff AND sample_failures_purged_at IS NULL
        AND sample_failures IS NOT NULL AND jsonb_typeof(sample_failures) <> 'null'

    -- observed_value half (#1253)
    UPDATE results SET observed_value = NULL
      WHERE created_at < cutoff
        AND jsonb_typeof(observed_value -> 'observed_value') = 'array'

`results.__table_args__` only indexed `run_id` and `check_id` — nothing
supporting either predicate — so both halves did a full sequential scan of an
unbounded table (one row per check per run, never row-deleted — ADR 0012
keeps `metric_value` for trends) every day to find a small set.

**Two indexes, not one — #323 review finding F1.** An earlier draft of this
migration shipped only `ix_results_unpurged_created` (the `sample_failures`
predicate) on the assumption it would help both halves. It cannot:
`observed_value`'s predicate has no `sample_failures_purged_at` term at all,
so it can never imply that index's WHERE clause — Postgres partial-index
usage requires the query to provably imply the index predicate by matching
expression trees, not by being "about the same table." Without its own
index, batching would have made this half *worse* than the pre-#323 code —
one full scan per chunk during a catch-up, instead of the one full scan the
old single UPDATE did.

**`ix_results_unpurged_created`'s predicate folds in the typeof guards —
#323 review finding F2.** An earlier draft used only `WHERE
sample_failures_purged_at IS NULL`, on the theory that a purged row leaves
the index and the working set stays small. False: a *passing* check's
`sample_failures` is SQL NULL from the moment the result is written (nothing
to redact — there's no reason to ever stamp `sample_failures_purged_at` on
it), so every passing-check row — most of the table, growing forever —
would sit in `WHERE sample_failures_purged_at IS NULL` permanently. The
index would grow ~linearly with `results` itself, and each sweep — each
*chunk* of each sweep, post-batching — would heap-fetch that entire
never-eligible prefix only to discard it on the typeof check, turning a
bounded catch-up into a superlinear one. Folding `sample_failures IS NOT
NULL AND jsonb_typeof(sample_failures) <> 'null'` into the index predicate
means only rows that ever *could* need purging enter the index at all, and
`run_service._purge_column`'s query WHERE is now built to match this
predicate's text exactly (not just imply a subset of it) — see that
function's docstring.

**CREATE INDEX CONCURRENTLY, so this cannot lock out writers.** `results` is
written by every check in every run; a plain CREATE INDEX takes a SHARE lock
for its whole duration, blocking every result insert — the same self-inflicted
#748-shaped incident the `00a938b64317` migration avoided on `runs`.
CONCURRENTLY cannot run inside a transaction block, which is what
`op.get_context().autocommit_block()` steps out for (see `env.py`'s
`transaction_per_migration=True`, added for exactly this).

**Self-healing retry — #323 review finding F3.** `env.py` sets a 15s
`lock_timeout` on the migration connection; CIC's wait phases are ordinary
lock waits, and the migrate job can run while an old-revision worker is
still writing `results`, so a >15s writer can cancel a CIC statement. A
cancelled CIC leaves an INVALID index behind that Postgres will never use —
and a naive `IF NOT EXISTS` retry treats "an index with this name exists" as
success, silently leaving the INVALID one in place forever with zero
signal. Each `CREATE INDEX CONCURRENTLY IF NOT EXISTS` is therefore preceded
by `DROP INDEX CONCURRENTLY IF EXISTS` on the same name, inside the same
autocommit block: a retry drops whatever (possibly INVALID) index is there
and rebuilds cleanly, rather than trusting a same-named index to be valid.

Consequences of CONCURRENTLY, stated because they are real:

* even with the self-heal above, a run that dies BETWEEN the DROP and the
  CREATE leaves the index briefly absent — never INVALID-and-silently-kept,
  which is the failure mode that actually matters (an index the planner
  can't use with no signal that it can't).
* it takes two table scans per index and is slower in wall-clock than the
  locking form. That is the trade: slower deploy, no write outage.

Backward compatible — an index is invisible to application code; the
`jsonb_typeof` guards in the sweep's WHERE clauses are unchanged in shape
(the `sample_failures` half's guard is now also mirrored in DDL, not just in
the query). Tested `alembic upgrade head` and `alembic downgrade -1` locally
against real Postgres, including a second `upgrade head` to confirm the
DROP-before-CREATE retry path is idempotent.

Rollback plan: `alembic downgrade -1` drops both indexes (also
CONCURRENTLY, so downgrade doesn't lock either); the sweep still functions
without them, just back to the pre-#323 sequential scans.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "fbf4fe92e295"
down_revision: str | None = "5ffa2405f9e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # autocommit_block: Postgres refuses CREATE INDEX CONCURRENTLY inside a
    # transaction block, so step out of alembic's for these statements. Each
    # CREATE is preceded by a same-name DROP (#323 review F3) so a retry
    # after a cancelled/INVALID CIC self-heals instead of silently keeping
    # an unusable index.
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
