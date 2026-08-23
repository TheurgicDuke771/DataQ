"""add workspace_health — workspace-level delivered-alert flags (#1052)"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "70a054d4c469"
down_revision: str | None = "00a938b64317"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_health",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("alerted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("workspace_health")
