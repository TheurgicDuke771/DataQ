"""per-suite Slack webhook + email recipients on suite_notifications"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d2e3f4a5b6c7"
down_revision: str | None = "c1d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "suite_notifications",
        sa.Column("slack_webhook_secret_ref", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "suite_notifications",
        sa.Column("email_recipients", sa.String(length=1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("suite_notifications", "email_recipients")
    op.drop_column("suite_notifications", "slack_webhook_secret_ref")
