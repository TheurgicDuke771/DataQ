"""add results.sampling — how much of the dataset a check actually saw (#595)"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4d23b47ee564"
down_revision: str | None = "fbf4fe92e295"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("results", sa.Column("sampling", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("results", "sampling")
