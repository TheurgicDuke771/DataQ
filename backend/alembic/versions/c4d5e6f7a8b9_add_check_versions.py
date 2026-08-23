"""add check_versions (per-check config history) (#280)"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d5e6f7a8b9"
down_revision: str | None = "b1f2c3d4e5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "check_versions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("check_id", UUID(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("expectation_type", sa.String(length=128), nullable=False),
        sa.Column("config", JSONB(), nullable=False),
        sa.Column("warn_threshold", sa.Numeric(), nullable=True),
        sa.Column("fail_threshold", sa.Numeric(), nullable=True),
        sa.Column("critical_threshold", sa.Numeric(), nullable=True),
        sa.Column("changed_by", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["check_id"], ["checks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["changed_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("check_id", "version_no", name="uq_check_versions_check_version"),
    )
    op.create_index("ix_check_versions_check_id", "check_versions", ["check_id"])


def downgrade() -> None:
    op.drop_index("ix_check_versions_check_id", table_name="check_versions")
    op.drop_table("check_versions")
