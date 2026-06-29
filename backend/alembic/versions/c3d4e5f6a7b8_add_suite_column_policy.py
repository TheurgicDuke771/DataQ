"""add suites.column_policy (failing-sample redaction policy)

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-29 00:00:00.000000+00:00

Adds ``suites.column_policy`` (JSONB) backing column-aware redaction of failing-
row samples (#415): ``{"identifier_column": str, "pii_columns": [str]}`` — the
identifier is shown so a failing row is locatable, ``pii_columns`` are masked,
and unclassified columns still default-redact.

Backward-compatible: an additive **nullable** column with no default and no data
rewrite. NULL = no policy → the existing blanket-mask fallback, so the migration
can deploy ahead of the code that reads it — no two-step required.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "suites",
        sa.Column("column_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("suites", "column_policy")
