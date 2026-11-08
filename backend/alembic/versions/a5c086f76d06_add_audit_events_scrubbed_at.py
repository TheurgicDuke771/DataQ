"""`audit_events.scrubbed_at` — when a payload path was scrubbed (#2547)

ADR 0041's 2026-11-08 amendment adds the one exception to append-only: an audited scrub of a
check event's `config`. The column is display-only (the audit page says "scrubbed"); the evidence
is the chained scrub event, so verification never reads it. It sits outside the row hash.

Additive: one nullable column, NULL for every existing row. No code reads or writes it yet; that
ships in the next PR, after this one is deployed. No grant: the scrub grants itself
`UPDATE (before, after, scrubbed_at)` for its own transaction only.

Rollback: `alembic downgrade -1` drops the column. Tested up, down and up again against a scratch
Postgres database.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a5c086f76d06"
down_revision: str | None = "ad8a8c3394b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "audit_events", sa.Column("scrubbed_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("audit_events", "scrubbed_at")
