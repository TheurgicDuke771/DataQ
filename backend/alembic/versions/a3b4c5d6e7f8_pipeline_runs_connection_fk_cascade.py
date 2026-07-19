"""pipeline_runs.connection_id → ON DELETE CASCADE (#753).

The FK was created with no ondelete (NO ACTION), so deleting an orchestration
connection that had ever been polled tripped the constraint and surfaced as an
unhandled 500. Pipeline-run rows are observations polled *through* the
connection — meaningless once it is gone (the same rationale as
`lineage_edges.connection_id`, which has cascaded since #757) — so the delete
now takes them along.

Backward-compatible: a pure widening of delete behavior (no column change, no
data rewrite); running code never relied on the delete failing — it 500'd.
Tested up + down locally; rollback restores the bare FK.

**Locking note** (migration-safety audit, mirroring `a9b0c1d2e3f4`): the
drop+add pair runs in one transaction, so DROP CONSTRAINT's ACCESS EXCLUSIVE
lock is held through ADD CONSTRAINT's FK-validation scan. **Measured before
merge, not assumed:** prod `pipeline_runs` is **211 rows / 176 kB**
(2026-07-19) — the scan is sub-millisecond, so the `NOT VALID` +
`VALIDATE CONSTRAINT` split is unnecessary here. It would also buy nothing as
the code stands: `alembic/env.py` wraps the whole upgrade in ONE transaction
(no `transaction_per_migration`), so splitting across revisions would not
release the lock between them — reach for the split only together with that
env change, and only once this table is genuinely large.

The residual risk was never the scan; it was *acquiring* the lock while the
pre-roll containers still poll every 10 minutes. That is now bounded at the
engine (`env.py` `lock_timeout`, this PR): a contended migration fails fast and
retryably instead of hanging the deploy (#854's lesson, which had only ever
been applied to the app engine).

**Rollout window** (accepted): between this migration and the api/worker roll,
*old* code — which has no #753 pre-check — will let `DELETE /connections/{id}`
succeed and cascade the connection's pipeline-run history instead of 500ing.
The window is minutes, the action is deliberate and admin-initiated, and the
new code that lands moments later 409s it with the dependents named.
"""

from __future__ import annotations

from alembic import op

revision = "a3b4c5d6e7f8"
down_revision = "b2c3d4e5f6a9"
branch_labels = None
depends_on = None

_FK = "fk_pipeline_runs_connection_id_connections"
_TABLE = "pipeline_runs"


def upgrade() -> None:
    op.drop_constraint(_FK, _TABLE, type_="foreignkey")
    op.create_foreign_key(_FK, _TABLE, "connections", ["connection_id"], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    op.drop_constraint(_FK, _TABLE, type_="foreignkey")
    op.create_foreign_key(_FK, _TABLE, "connections", ["connection_id"], ["id"])
