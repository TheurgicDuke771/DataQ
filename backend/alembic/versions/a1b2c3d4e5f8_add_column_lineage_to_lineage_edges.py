"""Add column-level lineage to lineage_edges (#901)."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "a1b2c3d4e5f8"
down_revision = "960d18679639"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("lineage_edges", sa.Column("columns", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("lineage_edges", "columns")
