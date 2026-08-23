"""add connection inventory-sync outcome state (#1104)"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c22fa93eb834"
down_revision: str | None = "6230293aea96"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "connections",
        sa.Column("inventory_sync_last_attempted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "connections",
        sa.Column("inventory_sync_last_error", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "connections",
        sa.Column("inventory_sync_failing_since", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("connections", "inventory_sync_failing_since")
    op.drop_column("connections", "inventory_sync_last_error")
    op.drop_column("connections", "inventory_sync_last_attempted_at")
