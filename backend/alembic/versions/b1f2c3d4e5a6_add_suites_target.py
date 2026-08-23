"""add suites.target (datasource-shaped run target) (#215)"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1f2c3d4e5a6"
down_revision: str | None = "784847178482"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("suites", sa.Column("target", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("suites", "target")
