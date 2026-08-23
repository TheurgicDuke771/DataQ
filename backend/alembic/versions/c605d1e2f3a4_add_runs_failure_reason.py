"""add runs.failure_reason (surface why a run failed, #605)"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c605d1e2f3a4"
down_revision: str | None = "d2e3f4a5b6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("failure_reason", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "failure_reason")
