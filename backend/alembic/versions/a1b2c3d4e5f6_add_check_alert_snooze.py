"""add checks.alert_snoozed_until (alert suppression / snooze)

Revision ID: a1b2c3d4e5f6
Revises: f7a8b9c0d1e2
Create Date: 2026-06-26 00:00:00.000000+00:00

Adds the ``checks.alert_snoozed_until`` column backing alert suppression: mute a
specific check's alerts until a moment (UTC). NULL / past = active.

Backward-compatible: an additive **nullable** column with no default and no data
rewrite. Existing code ignores it (old rows read NULL = not snoozed), so the
migration can deploy ahead of the code that reads it — no two-step required.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "checks",
        sa.Column("alert_snoozed_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("checks", "alert_snoozed_until")
