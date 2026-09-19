"""Add runs.queued_reason — why a queued run is still queued (#1998)

Additive and nullable, so the running image (which never writes it) is unaffected: a NULL
reads as the ordinary case, a run waiting on the broker.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c9be17299107"
down_revision: str | None = "d55c00ef4f0a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("queued_reason", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "queued_reason")
