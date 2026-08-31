"""add webhook channel payload_template + auth header (#1663)

Additive only: three new nullable columns on `notification_channels`
(`payload_template`, `auth_header_name`, `auth_header_secret_ref`).
`None`/unset on all three means the unmodified generic webhook payload
and no extra auth header — the pre-#1663 behavior, unchanged.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "cadc40254699"
down_revision: str | None = "50f1a89bbc11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("notification_channels", sa.Column("payload_template", JSONB, nullable=True))
    op.add_column(
        "notification_channels", sa.Column("auth_header_name", sa.String(length=128), nullable=True)
    )
    op.add_column(
        "notification_channels",
        sa.Column("auth_header_secret_ref", sa.String(length=256), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("notification_channels", "auth_header_secret_ref")
    op.drop_column("notification_channels", "auth_header_name")
    op.drop_column("notification_channels", "payload_template")
