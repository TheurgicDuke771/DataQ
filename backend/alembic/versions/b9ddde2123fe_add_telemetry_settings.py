"""telemetry_settings — the usage-telemetry opt-in as a workspace setting (#1707)

Additive: one new singleton table. Nothing is dropped and no existing column changes
meaning. The table ships EMPTY, and an absent row resolves the same as `enabled = false`,
so this migration changes no behaviour on any deployment: usage telemetry stays off until
an admin opts in AND the operator configures a sink. No code reads the table yet.

Rollback: `alembic downgrade -1` drops the table. Tested up, down and up again against a
scratch Postgres database.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b9ddde2123fe"
down_revision: str | None = "b94674780cb8"
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
