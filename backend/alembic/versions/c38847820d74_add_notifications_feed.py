"""add the in-app notifications feed (#1687)

One row per recipient per published run outcome; `read_at` is the recipient's own state.
Additive: a new table and its indexes, nothing existing changes, so the running image
deploys ahead of (or with) the code that writes it.

Rollback: downgrade drops the table — the feed is a derived view of runs/incidents that
the other alert channels already delivered, so nothing is lost that another table does
not hold.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c38847820d74"
down_revision: str | None = "192fc7db5596"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SEVERITIES = "'pass', 'warn', 'fail', 'critical', 'error'"


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("suite_id", UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), nullable=True),
        sa.Column("incident_id", UUID(as_uuid=True), nullable=True),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("deep_link", sa.String(length=512), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"severity IN ({_SEVERITIES})", name="notification_severity_valid"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["suite_id"], ["suites.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_notifications_user_created",
        "notifications",
        ["user_id", sa.text("created_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "ix_notifications_user_unread",
        "notifications",
        ["user_id", sa.text("created_at DESC"), sa.text("id DESC")],
        postgresql_where=sa.text("read_at IS NULL"),
    )
    op.create_index("ix_notifications_suite_id", "notifications", ["suite_id"])
    op.create_index("ix_notifications_incident_id", "notifications", ["incident_id"])
    op.create_index(
        "uq_notifications_user_run",
        "notifications",
        ["user_id", "run_id"],
        unique=True,
        postgresql_where=sa.text("run_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_notifications_user_run", table_name="notifications")
    op.drop_index("ix_notifications_incident_id", table_name="notifications")
    op.drop_index("ix_notifications_suite_id", table_name="notifications")
    op.drop_index("ix_notifications_user_unread", table_name="notifications")
    op.drop_index("ix_notifications_user_created", table_name="notifications")
    op.drop_table("notifications")
