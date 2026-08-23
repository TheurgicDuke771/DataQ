"""check engine seam — ADR 0036 slice 1 (#895)"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "9a9ccf49cc8c"
down_revision = "675158c4333e"
branch_labels = None
depends_on = None

_ENGINES = ("gx", "dmf", "dqx", "dataplex")


def upgrade() -> None:
    op.add_column(
        "checks",
        sa.Column("engine", sa.String(32), nullable=False, server_default=sa.text("'gx'")),
    )
    op.create_check_constraint(
        "engine_valid",
        "checks",
        "engine IN (" + ", ".join(f"'{e}'" for e in _ENGINES) + ")",
    )
    op.add_column(
        "check_versions",
        sa.Column("engine", sa.String(32), nullable=False, server_default=sa.text("'gx'")),
    )
    op.add_column(
        "connections",
        sa.Column("engine_capabilities", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("connections", "engine_capabilities")
    op.drop_column("check_versions", "engine")
    op.drop_constraint("engine_valid", "checks", type_="check")
    op.drop_column("checks", "engine")
