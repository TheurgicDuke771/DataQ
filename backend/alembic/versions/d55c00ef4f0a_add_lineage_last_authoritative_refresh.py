"""connections.lineage_last_authoritative_refresh_at — the prune-suspension marker (#1236)

A snapshot lineage pull that only PARTIALLY observed current state persists
accrete-only and skips its stale-edge prune. Nothing enforced that a clean pull
ever arrives, so a persistent-but-unclassifiable condition could suspend pruning
forever while `lineage_edges` accreted — reported nowhere but a log line.

This column records when the cache was last reconciled (pruned) against an
observation, which is both the backstop's clock and the age an operator needs to
tell "blipped once last night" from "has not pruned in three weeks".

Backfill: existing snapshot-source connections that have refreshed without error
get `lineage_last_refresh_at`. The alternative — leaving them NULL — would make
every already-healthy Snowflake connection report "has never pruned" on the
deploy. The approximation is one-time and self-correcting on the NEXT refresh, not a
staleness window later: `lineage_last_refresh_at` advances every cycle while this stamp
advances only on a pull that actually pruned, so a still-suspended connection re-reports
as suspended one refresh interval after the deploy.

`ADD COLUMN` and the backfill share one transaction. That is the shape rule 9 exists to
catch, and it is left as-is deliberately: a nullable `ADD COLUMN` is metadata-only (no
rewrite) and `connections` holds tens of rows, so the combined statement is sub-second.

NULL after this migration means "no prune has ever been recorded for this
connection" — the backstop deliberately does NOT fire on it, because a
first-ever partial pull would delete edges accreted from earlier partial pulls
against an observation never shown to be complete.

Additive: one nullable column plus a backfill of it. No existing column is read
differently by old code.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d55c00ef4f0a"
down_revision: str | None = "d416af4f8ad3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMN = "lineage_last_authoritative_refresh_at"

# Snapshot-source types only: an incremental source never prunes, so stamping one
# would make "has never pruned" read as a fault on a connection working correctly.
_BACKFILL_SQL = """
UPDATE connections
   SET lineage_last_authoritative_refresh_at = lineage_last_refresh_at
 WHERE type = 'snowflake'
   AND lineage_last_refresh_at IS NOT NULL
   AND lineage_last_error IS NULL
   AND lineage_last_authoritative_refresh_at IS NULL
"""


def upgrade() -> None:
    op.add_column(
        "connections",
        sa.Column(_COLUMN, sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(_BACKFILL_SQL)


def downgrade() -> None:
    op.drop_column("connections", _COLUMN)
