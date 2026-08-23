"""Record orchestration-poll health on the connection (#828)."""

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
