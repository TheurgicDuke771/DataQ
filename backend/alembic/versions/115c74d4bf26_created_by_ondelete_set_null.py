"""created_by: nullable + ON DELETE SET NULL on the three user FKs (#1319)"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "115c74d4bf26"
down_revision: str | None = "ecda713656ac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: (table, constraint name) for each edge.
_EDGES: tuple[tuple[str, str], ...] = (
    ("connections", "fk_connections_created_by_users"),
    ("suites", "fk_suites_created_by_users"),
    ("schedules", "fk_schedules_created_by_users"),
)


def upgrade() -> None:
    for table, constraint in _EDGES:
        op.alter_column(table, "created_by", existing_type=sa.UUID(), nullable=True)
        op.drop_constraint(constraint, table, type_="foreignkey")
        op.create_foreign_key(
            constraint, table, "users", ["created_by"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    for table, constraint in _EDGES:
        op.drop_constraint(constraint, table, type_="foreignkey")
        op.create_foreign_key(constraint, table, "users", ["created_by"], ["id"])
        # Deliberately NOT backfilled.
        op.alter_column(table, "created_by", existing_type=sa.UUID(), nullable=False)
