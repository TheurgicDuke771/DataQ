"""add notification_channels + suite_notification_channels (#1514)

Additive only: existing `suite_notifications` per-suite webhook/Slack/email fields
are untouched and stay fully functional — a suite may keep using them, link
channels, or both.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "a3f5c7e9b1d2"
down_revision: str | None = "dd652ae1ef85"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification_channels",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("webhook_secret_ref", sa.String(length=256), nullable=True),
        sa.Column("email_recipients", sa.String(length=1024), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "type IN ('teams', 'slack', 'email')", name="notification_channel_type_valid"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "suite_notification_channels",
        sa.Column("suite_id", UUID(as_uuid=True), nullable=False),
        sa.Column("channel_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["suite_id"], ["suites.id"], ondelete="CASCADE"),
        # RESTRICT is the DB-level backstop for the application delete guard (a
        # channel with live links must be unlinked first) — same pairing as the
        # comparison-source FK on `checks` (ADR 0015).
        sa.ForeignKeyConstraint(["channel_id"], ["notification_channels.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("suite_id", "channel_id"),
    )
    # The dependent-suites lookup on channel delete/rotate scans by channel_id.
    op.create_index(
        "ix_suite_notification_channels_channel_id",
        "suite_notification_channels",
        ["channel_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_suite_notification_channels_channel_id", table_name="suite_notification_channels"
    )
    op.drop_table("suite_notification_channels")
    op.drop_table("notification_channels")
