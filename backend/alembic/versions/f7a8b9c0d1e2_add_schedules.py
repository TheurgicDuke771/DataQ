"""add schedules (cron-driven suite run schedules — A7)

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-06-20 00:00:00.000000+00:00

Adds the ``schedules`` table backing the scheduling backend (A7): a cron
expression + IANA timezone per suite that the beat dispatcher
(``worker.tasks.dispatch_due_schedules``) fires on. ``next_run_at`` is the
precomputed next fire (UTC); the dispatcher scans ``enabled, next_run_at`` (hot
path) and only ever parses cron when a schedule actually fires. A schedule is
cascade-deleted with its suite.

Backward-compatible: a brand-new table, no change to existing tables, no data
rewrite, no two-step.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7a8b9c0d1e2"
down_revision: str | None = "e6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "schedules",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("suite_id", UUID(as_uuid=True), nullable=False),
        sa.Column("cron", sa.String(length=128), nullable=False),
        sa.Column(
            "timezone", sa.String(length=64), server_default=sa.text("'UTC'"), nullable=False
        ),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["suite_id"], ["suites.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_schedules_suite_id", "schedules", ["suite_id"])
    op.create_index("ix_schedules_enabled_next_run_at", "schedules", ["enabled", "next_run_at"])


def downgrade() -> None:
    op.drop_index("ix_schedules_enabled_next_run_at", table_name="schedules")
    op.drop_index("ix_schedules_suite_id", table_name="schedules")
    op.drop_table("schedules")
