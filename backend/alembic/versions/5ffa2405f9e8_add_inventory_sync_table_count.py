"""add connection inventory-sync table-count + zero-drop state (#1242)"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5ffa2405f9e8"
down_revision: str | None = "c22fa93eb834"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "connections",
        sa.Column("inventory_sync_last_table_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "connections",
        sa.Column("inventory_sync_zero_since", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("connections", "inventory_sync_zero_since")
    op.drop_column("connections", "inventory_sync_last_table_count")
