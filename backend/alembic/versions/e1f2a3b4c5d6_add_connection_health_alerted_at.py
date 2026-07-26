"""Add connections.health_alerted_at — poll-health alert delivery state (#843).

Revision ID: e1f2a3b4c5d6
Revises: f1a2b3c4d5e6
Create Date: 2026-07-25

The poll-health alert edges were driven off the failure COUNTER: fire when the
streak equals the threshold, recover when the cleared streak had reached it. But
delivery is best-effort by design — a channel can be down, a webhook unresolved,
a secret missing, and each is a quiet no-op — so an operator could receive an
unprompted "orchestration poll recovered" for an alarm they were never told
about. This column records when a FAILING alert was actually *delivered*; NULL
means none is outstanding, and both edges now ride it.

It also fixes the threshold-change caveat the old code documented: a connection
already past a newly-lowered threshold never lands on `==` again, so it never
alerted at all. Keyed on delivery state, the crossing test becomes `>=`.

**Backward compatible, no two-step needed.** Nullable with no server default and
no backfill: the currently-deployed code neither reads nor writes it, so it keeps
working unchanged against this schema. Pure widening — no data rewrite, no
narrowing of an existing column.

Existing rows are deliberately NOT backfilled. Writing a timestamp would claim an
alert was delivered that we have no record of, and would suppress the very first
real crossing after deploy. NULL — "no alert outstanding" — is both true and the
safe default: a connection already failing alerts once on the next sweep.

Tested up and down locally. Down drops the column, losing any outstanding-alert
state; the consequence is at most one duplicate failing alert per connection
after a rollback, which is the harmless direction.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "connections",
        sa.Column("health_alerted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("connections", "health_alerted_at")
