"""add warehouse-lineage refresh state to connections (#858)"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "960d18679639"
down_revision: str | None = "e2f3a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive, backward-compatible (#858): five nullable columns holding the warehouse-lineage
    # beat's per-connection state.
    op.add_column(
        "connections", sa.Column("lineage_watermark", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "connections",
        sa.Column("lineage_last_refresh_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "connections", sa.Column("lineage_last_tier", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "connections", sa.Column("lineage_degraded_reason", sa.String(length=512), nullable=True)
    )
    op.add_column(
        "connections", sa.Column("lineage_last_error", sa.String(length=512), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("connections", "lineage_last_error")
    op.drop_column("connections", "lineage_degraded_reason")
    op.drop_column("connections", "lineage_last_tier")
    op.drop_column("connections", "lineage_last_refresh_at")
    op.drop_column("connections", "lineage_watermark")
