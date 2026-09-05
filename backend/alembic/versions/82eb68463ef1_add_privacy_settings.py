"""privacy_settings — zero-sample mode as a workspace setting (#1887)

Additive: one new singleton table. Nothing is dropped and no existing column
changes meaning. The table ships EMPTY, and an absent row resolves the same as
`zero_sample_mode = false`, so this migration changes no behaviour on any
deployment — `PRIVACY_ZERO_SAMPLE_MODE` remains the fail-safe floor and the
effective value is `env OR row`.

Rollback: `alembic downgrade -1` drops the table. Tested up, down and up again
against a scratch Postgres database.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "82eb68463ef1"
down_revision: str | None = "0451ebdc77f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "privacy_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "zero_sample_mode", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "updated_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("privacy_settings")
