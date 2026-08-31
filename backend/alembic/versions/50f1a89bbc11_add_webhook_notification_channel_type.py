"""add webhook notification_channel type (#1662)

Additive only: two new nullable columns on `notification_channels`
(`webhook_url`, `hmac_secret_ref`) and the `type` CHECK widened to admit
'webhook'. Existing teams/slack/email channels and rows are untouched.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "50f1a89bbc11"
down_revision: str | None = "a3f5c7e9b1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "notification_channels", sa.Column("webhook_url", sa.String(length=2048), nullable=True)
    )
    op.add_column(
        "notification_channels", sa.Column("hmac_secret_ref", sa.String(length=256), nullable=True)
    )
    op.drop_constraint("notification_channel_type_valid", "notification_channels", type_="check")
    op.create_check_constraint(
        "notification_channel_type_valid",
        "notification_channels",
        "type IN ('teams', 'slack', 'email', 'webhook')",
    )


def downgrade() -> None:
    op.drop_constraint("notification_channel_type_valid", "notification_channels", type_="check")
    op.create_check_constraint(
        "notification_channel_type_valid",
        "notification_channels",
        "type IN ('teams', 'slack', 'email')",
    )
    op.drop_column("notification_channels", "hmac_secret_ref")
    op.drop_column("notification_channels", "webhook_url")
