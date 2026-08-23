"""add runs.celery_task_id (cancel support, A2)"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5e6f7a8b9c0"
down_revision: str | None = "c4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("celery_task_id", sa.String(length=155), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "celery_task_id")
