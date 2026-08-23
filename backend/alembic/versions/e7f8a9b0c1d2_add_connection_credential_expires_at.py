"""Add `connections.credential_expires_at` — warn before a credential expires (#838)."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e7f8a9b0c1d2"
down_revision: str | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "connections",
        sa.Column("credential_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("connections", "credential_expires_at")
