"""Add the DQ-dimension classification column to checks + check_versions (#124)."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: str | None = "a3b4c5d6e7f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Kept as a literal rather than imported from `models.DQ_DIMENSIONS`: a migration must describe the
# schema at ITS point in history.
_DIMENSIONS = (
    "accuracy",
    "completeness",
    "consistency",
    "integrity",
    "timeliness",
    "uniqueness",
    "validity",
)


def upgrade() -> None:
    op.add_column("checks", sa.Column("dimension", sa.String(length=32), nullable=True))
    op.add_column("check_versions", sa.Column("dimension", sa.String(length=32), nullable=True))
    quoted = ", ".join(f"'{d}'" for d in _DIMENSIONS)
    op.create_check_constraint("dimension_valid", "checks", f"dimension IN ({quoted})")


def downgrade() -> None:
    op.drop_constraint("dimension_valid", "checks", type_="check")
    op.drop_column("check_versions", "dimension")
    op.drop_column("checks", "dimension")
