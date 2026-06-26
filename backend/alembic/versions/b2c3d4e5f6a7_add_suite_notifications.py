"""add suite_notifications (per-suite alert delivery config)

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-26 00:00:00.000000+00:00

Adds the ``suite_notifications`` table backing per-suite alert config: whether a
suite's outcomes are delivered (``enabled``), the threshold (``alert_on`` —
fail / warn / always), and an optional per-suite Teams webhook referenced by
``webhook_secret_ref`` (the token-bearing URL lives in the SecretStore). One row
per suite (unique), cascade-deleted with the suite.

Backward-compatible: a brand-new table, no change to existing tables, no data
rewrite, no two-step. Suites with no row use the default policy.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "suite_notifications",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("suite_id", UUID(as_uuid=True), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "alert_on", sa.String(length=16), server_default=sa.text("'warn'"), nullable=False
        ),
        sa.Column("webhook_secret_ref", sa.String(length=256), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("alert_on IN ('fail', 'warn', 'always')", name="alert_on_valid"),
        sa.ForeignKeyConstraint(["suite_id"], ["suites.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("suite_id", name="uq_suite_notifications_suite_id"),
    )


def downgrade() -> None:
    op.drop_table("suite_notifications")
