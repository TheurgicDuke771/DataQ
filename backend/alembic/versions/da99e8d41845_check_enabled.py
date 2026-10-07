"""checks.enabled and check_versions.enabled: turn a check off without deleting it (#2369)

Additive: both columns are NOT NULL with a server default of true, so every existing check
and every existing version reads as enabled, which is what they were. Nothing reads the
columns yet; the code that does ships after this is deployed.

Rollback: downgrade drops both columns; every check runs again, as before.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "da99e8d41845"
down_revision: str | None = "49bd96efd9fc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("checks", "check_versions")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "enabled")
