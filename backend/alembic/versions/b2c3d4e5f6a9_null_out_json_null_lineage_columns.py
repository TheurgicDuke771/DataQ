"""Convert JSON-null lineage_edges.columns to SQL NULL (#907 data fix).

Revision ID: b2c3d4e5f6a9
Revises: a1b2c3d4e5f8
Create Date: 2026-07-18

The #903 bulk upsert serialized Python ``None`` as JSON ``null`` (SQLAlchemy JSONB
without ``none_as_null``), which is not SQL NULL — it passes ``IS NOT NULL`` filters
and 500'd every asset page whose neighbourhood touched such an edge (the first prod
Snowflake refresh wrote 339 of them). The ORM type now carries ``none_as_null=True``;
this backfills the rows written before the fix. Idempotent, backward-compatible
(the value means the same thing on both sides: "no pairs observed"), no-op on a
fresh database. Downgrade is a deliberate no-op — re-manufacturing the defect
would be the only thing it could do.
"""

from __future__ import annotations

from alembic import op

revision = "b2c3d4e5f6a9"
down_revision = "a1b2c3d4e5f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE lineage_edges SET columns = NULL WHERE columns = 'null'::jsonb")


def downgrade() -> None:
    pass
