"""Add connections.health_alerted_at — poll-health alert delivery state (#843)."""

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
