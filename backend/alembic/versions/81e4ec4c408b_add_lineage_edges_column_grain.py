"""Add lineage_edges.column_grain — did a pull look at this edge's column grain (#1710)

Additive and nullable, so the running image (which never reads or writes it) is unaffected. NULL
means "never recorded" and renders as unknown — never as "no column lineage exists".

Rollback: downgrade drops the column; the only reader is the column-coverage label, which then
reads every warehouse edge without pairs as "unknown", the pre-#1710 honesty level.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "81e4ec4c408b"
down_revision: str | None = "c9be17299107"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("lineage_edges", sa.Column("column_grain", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("lineage_edges", "column_grain")
