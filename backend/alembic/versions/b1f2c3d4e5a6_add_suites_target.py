"""add suites.target (datasource-shaped run target) (#215)

Revision ID: b1f2c3d4e5a6
Revises: 784847178482
Create Date: 2026-06-11 00:00:00.000000+00:00

A suite carried no **target** — which table / flat-file path / Unity Catalog
3-level name its checks run against — so no run path could dispatch (the
pipeline-success trigger created queued ``Run`` rows then logged
``suite_dispatch_deferred``). This adds a single nullable JSONB ``target`` on
``suites``, shaped like the column-profiler request
(``table`` / ``schema`` / ``catalog`` / ``path`` / ``file_format``), resolved
per connection type to the runner's ``(table, schema, catalog)`` triple
(``run_target.resolve_target``).

Backward-compatible: additive nullable column, no data rewrite, no two-step.
Existing suites are simply targetless (NULL) until one is set, at which point
they become runnable.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1f2c3d4e5a6"
down_revision: str | None = "784847178482"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("suites", sa.Column("target", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("suites", "target")
