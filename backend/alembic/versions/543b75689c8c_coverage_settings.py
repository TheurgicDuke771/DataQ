"""coverage_settings + assets.auto_coverage_config — automatic-coverage sensitivity (#1661)

Additive: one new singleton table and one nullable column. Nothing is dropped and no
existing column changes meaning. The table ships EMPTY and the column NULL, and both absent
resolve to the built-in defaults (ADR 0047, 2026-10-13 amendment), so this migration changes
no check's verdict on any deployment. No code reads either yet.

Rollback: `alembic downgrade -1` drops the column and the table. Tested up, down and up
again against a scratch Postgres database.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "543b75689c8c"
down_revision: str | None = "da99e8d41845"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "coverage_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column(
            "updated_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("id = 1", name="singleton"),
        sa.CheckConstraint("jsonb_typeof(config) = 'object'", name="config_is_object"),
    )
    op.add_column("assets", sa.Column("auto_coverage_config", postgresql.JSONB(), nullable=True))
    # The column is all NULL, so the check validates instantly and never scans real data.
    op.create_check_constraint(
        op.f("ck_assets_auto_coverage_config_is_object"),
        "assets",
        "auto_coverage_config IS NULL OR jsonb_typeof(auto_coverage_config) = 'object'",
    )


def downgrade() -> None:
    op.drop_column("assets", "auto_coverage_config")
    op.drop_table("coverage_settings")
