"""assets: cache warehouse column classifications — G3 (#433)"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "675158c4333e"
down_revision: str | None = "115c74d4bf26"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column("column_tags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "assets",
        sa.Column("column_tags_refreshed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assets", "column_tags_refreshed_at")
    op.drop_column("assets", "column_tags")
