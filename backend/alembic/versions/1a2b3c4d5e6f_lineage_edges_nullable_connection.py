"""lineage_edges: nullable connection_id + partial uniq for catalog-pull (ADR 0034, #762)"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1a2b3c4d5e6f"
# NOTE: chained off the current head at build time (f0a1b2c3d4e5, "add_asset_description").
down_revision: str | None = "c4e5a6b7d8f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PARTIAL_UQ = "uq_lineage_edges_up_down_source_nullconn"


def upgrade() -> None:
    op.alter_column("lineage_edges", "connection_id", nullable=True)
    op.create_index(
        _PARTIAL_UQ,
        "lineage_edges",
        ["upstream_asset_id", "downstream_asset_id", "source"],
        unique=True,
        postgresql_where=sa.text("connection_id IS NULL"),
    )


def downgrade() -> None:
    # Connection-less (pulled) edges can't satisfy a NOT NULL connection_id — drop them
    # first so the column can be restored to NOT NULL (a real, tested down path).
    op.execute("DELETE FROM lineage_edges WHERE connection_id IS NULL")
    op.drop_index(_PARTIAL_UQ, table_name="lineage_edges")
    op.alter_column("lineage_edges", "connection_id", nullable=False)
