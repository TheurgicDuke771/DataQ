"""pipeline_runs.connection_id → ON DELETE CASCADE (#753)."""

from __future__ import annotations

from alembic import op

revision = "a3b4c5d6e7f8"
down_revision = "b2c3d4e5f6a9"
branch_labels = None
depends_on = None

_FK = "fk_pipeline_runs_connection_id_connections"
_TABLE = "pipeline_runs"


def upgrade() -> None:
    op.drop_constraint(_FK, _TABLE, type_="foreignkey")
    op.create_foreign_key(_FK, _TABLE, "connections", ["connection_id"], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    op.drop_constraint(_FK, _TABLE, type_="foreignkey")
    op.create_foreign_key(_FK, _TABLE, "connections", ["connection_id"], ["id"])
