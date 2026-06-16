"""add check_versions (per-check config history) (#280)

Revision ID: c4d5e6f7a8b9
Revises: b1f2c3d4e5a6
Create Date: 2026-06-15 00:00:00.000000+00:00

Checks carried no history — ``check_service.update_check`` overwrote the row in
place, so a prior config was unrecoverable. This adds a ``check_versions`` table
holding an immutable snapshot of a check's editable state, written on create and
after every successful update; it backs the "version history" drawer ("see
previous config before overwriting"). This is per-check config history, not the
cross-entity audit log (deferred to v1.1).

``version_no`` is a per-check sequence (unique with ``check_id``). A version is
cascade-deleted with its check, but survives its author (``changed_by`` is
``SET NULL`` so a removed user doesn't drop the snapshot).

Backward-compatible: a brand-new table, no change to existing tables, no data
rewrite, no two-step. Existing checks simply have no recorded history until
their next create/update writes one.
"""

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
