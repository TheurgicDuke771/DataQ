"""scoring_settings — admin-configurable health-score weights (#1559)

Additive: one new singleton table. Nothing is dropped and no existing column
changes meaning. The table ships EMPTY, and an absent row resolves to the
ADR 0005 defaults (warn 0.5 / fail 1.0 / critical 2.0), so this migration
changes no score on any deployment.

Rollback: `alembic downgrade -1` drops the table. Tested up, down and up again
against a scratch Postgres database.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "2017e2c7ee11"
down_revision: str | None = "82eb68463ef1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scoring_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("warn_weight", sa.Numeric(5, 2), nullable=False),
        sa.Column("fail_weight", sa.Numeric(5, 2), nullable=False),
        sa.Column("critical_weight", sa.Numeric(5, 2), nullable=False),
        sa.Column(
            "updated_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "warn_weight >= 0 AND warn_weight <= fail_weight "
            "AND fail_weight <= critical_weight AND critical_weight > 0",
            name="weights_ordered",
        ),
    )


def downgrade() -> None:
    op.drop_table("scoring_settings")
