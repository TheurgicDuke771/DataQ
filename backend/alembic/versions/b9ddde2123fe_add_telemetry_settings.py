"""telemetry_settings — the usage-telemetry opt-in as a workspace setting (#1707)

Additive: one new singleton table, shipped empty; an absent row reads as `enabled = false`.
Rollback: `alembic downgrade -1` drops the table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b9ddde2123fe"
down_revision: str | None = "094076e0e7c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telemetry_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "updated_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("id = 1", name="singleton"),
    )


def downgrade() -> None:
    op.drop_table("telemetry_settings")
