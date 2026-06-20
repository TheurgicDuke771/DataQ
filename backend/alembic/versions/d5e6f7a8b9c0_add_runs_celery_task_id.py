"""add runs.celery_task_id (cancel support, A2)

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-06-20 00:00:00.000000+00:00

A run carried no link to the Celery task executing it, so a cancel couldn't
revoke a still-queued task. This adds a single nullable ``celery_task_id`` on
``runs``, captured at dispatch (``run_dispatch.dispatch_run``). The cancel
endpoint best-effort revokes it (drops a queued task); an in-flight run is
stopped cooperatively (the worker checks for a ``cancelled`` status).

Backward-compatible: additive nullable column, no data rewrite, no two-step.
Existing runs keep NULL (they predate cancel support and are already terminal).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5e6f7a8b9c0"
down_revision: str | None = "c4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("celery_task_id", sa.String(length=155), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "celery_task_id")
