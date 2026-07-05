"""per-suite Slack webhook + email recipients on suite_notifications

Adds per-suite overrides for the Slack and email channels (#633), mirroring the
existing per-suite Teams ``webhook_secret_ref``. Both columns are **nullable and
additive** — NULL means "fall back to the workspace-level config" (a rotated Slack
webhook / ``EMAIL_TO``), so every existing row keeps its current behaviour and old
code that never reads the columns is unaffected. Fully backward-compatible.

* ``slack_webhook_secret_ref`` — SecretStore key for the per-suite Slack webhook
  URL (token-bearing, so only the ref is stored), same shape as the Teams ref.
* ``email_recipients`` — comma-separated addresses (not a secret), stored inline.

Tested up + down locally. ``downgrade`` drops both columns; safe because the
publishers treat a missing per-suite override as "use the workspace fallback", so
rolling back only loses the per-suite overrides (delivery continues on workspace
config). No data migration.
"""

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
