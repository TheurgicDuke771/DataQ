"""checks.ordinal: the authoring order of a suite's checks (#1334)

Checks inserted in one transaction (an import, a seed) share a ``created_at``, so that
column cannot order them as written. A nullable add, backfilled here from the order every
surface shows today (``created_at NULLS LAST, id``) so nothing visibly moves. NULL stays
legal and sorts last, which is where code that predates the column leaves a new check.

Rollback: downgrade drops the column; listings fall back to ``created_at, id``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "49bd96efd9fc"
down_revision: str | None = "bf15e3a01a5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("checks", sa.Column("ordinal", sa.Integer(), nullable=True))
    op.execute("""
        UPDATE checks AS c
        SET ordinal = numbered.rn
        FROM (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY suite_id ORDER BY created_at NULLS LAST, id
                   ) AS rn
            FROM checks
        ) AS numbered
        WHERE numbered.id = c.id
        """)


def downgrade() -> None:
    op.drop_column("checks", "ordinal")
