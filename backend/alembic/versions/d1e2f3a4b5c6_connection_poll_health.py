"""Record orchestration-poll health on the connection (#828).

Revision ID: d1e2f3a4b5c6
Revises: c9d0e1f2a3b4
Create Date: 2026-07-13

A failing orchestration poll was invisible in the product. It logged
`orchestration_poll_failed` at ERROR every 10 minutes and moved on: the connection
still rendered as configured, the lineage UI showed its ordinary empty state
(indistinguishable from "this asset genuinely has no upstreams"), and the beat task
returned success. Prod lineage was dark for six days on an expired ADLS SAS and nothing
in the app said so.

These three additive columns make the poll's outcome a **fact about the connection**:

- ``last_polled_at``            — when the poll last ran (NULL = never polled).
- ``last_poll_error``           — a CLASSIFIED, redaction-safe reason (NULL = healthy).
                                  Never raw exception text: a transport error can carry
                                  a SAS token or a DSN.
- ``consecutive_poll_failures`` — reset to 0 on any success. A counter, not a boolean,
                                  so the UI can say "failing for ~6 days".

**Backward-compatible by construction** (CLAUDE.md §6): all three are nullable or carry
a server default, so the currently-deployed code — which never writes them — keeps
working against this schema. The code that reads them ships after this migration is
applied (two-step deploy).

Down-migration drops them; no data outside these columns is touched.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "d1e2f3a4b5c6"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "connections",
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "connections",
        sa.Column("last_poll_error", sa.String(length=512), nullable=True),
    )
    # NOT NULL is safe *with* the server default: existing rows backfill to 0, and the
    # deployed code that doesn't know the column still inserts fine.
    op.add_column(
        "connections",
        sa.Column(
            "consecutive_poll_failures",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("connections", "consecutive_poll_failures")
    op.drop_column("connections", "last_poll_error")
    op.drop_column("connections", "last_polled_at")
