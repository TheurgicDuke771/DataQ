"""datasource credential-health columns on `connections` (#1697)

Additive and backward-compatible: four nullable/defaulted columns, no data
rewrite, no constraint on existing rows. Code that reads them ships after this
migration is deployed (two-step rule). Both timestamps NULL on every existing
row is the deliberate initial state — it renders as "unknown", never "healthy".

Rollback: `alembic downgrade -1` drops the four columns; nothing else references
them, and the run/test paths that write them tolerate their absence only in the
sense that the code reading them must be rolled back first (standard two-step).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c2f5dc250791"
down_revision: str | None = "6bcf67868753"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "connections", sa.Column("last_auth_failure_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "connections", sa.Column("last_auth_success_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "connections",
        sa.Column(
            "consecutive_auth_failures",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column("connections", sa.Column("last_auth_error", sa.String(512), nullable=True))


def downgrade() -> None:
    op.drop_column("connections", "last_auth_error")
    op.drop_column("connections", "consecutive_auth_failures")
    op.drop_column("connections", "last_auth_success_at")
    op.drop_column("connections", "last_auth_failure_at")
