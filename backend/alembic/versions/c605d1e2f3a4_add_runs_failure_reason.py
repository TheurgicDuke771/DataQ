"""add runs.failure_reason (surface why a run failed, #605)

Revision ID: c605d1e2f3a4
Revises: d2e3f4a5b6c7
Create Date: 2026-07-07 00:00:00.000000+00:00

A `failed` run previously showed a bare status with no user-visible reason — the
runner exception was logged server-side only. This adds a single nullable
``failure_reason`` on ``runs`` carrying a redaction-safe, classified message
(``failure_classifier`` — a fixed per-category string, never raw adapter text).

Backward-compatible: additive nullable column, no data rewrite, no two-step.
Existing runs keep NULL (they predate the classifier); the app reads NULL as
"no reason recorded" and falls back to the bare status.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c605d1e2f3a4"
down_revision: str | None = "d2e3f4a5b6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("failure_reason", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "failure_reason")
