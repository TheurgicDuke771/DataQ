"""add assets.description (asset-metadata mutation, G-d phase 2)"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f0a1b2c3d4e5"
down_revision: str | None = "a1c2e3d4f5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("assets", sa.Column("description", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("assets", "description")
