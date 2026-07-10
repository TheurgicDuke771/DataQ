"""add lineage_edges cache (G-d dbt-manifest lineage, #759)

ADR 0034 slice 2: a small `lineage_edges` cache of external truth — directed
`upstream_asset_id → downstream_asset_id` edges keyed on
`(upstream, downstream, source, connection_id)`, refreshed from the parsed dbt
`manifest.json` (and, later, other lineage sources) by the connection whose
refresh surfaced the edge. Blast radius walks these edges downstream from a
failing asset.

Additive & backward-compatible (CLAUDE.md migration rules): a brand-new table
only; all three FKs CASCADE-delete — both endpoint assets (an edge is meaningless
without either asset) and the refreshing `connection_id` (provenance + prune
scope, NOT NULL so one project's prune never touches another's edges). No existing
read path changes. `source` is un-CHECKed on purpose — lineage sources will grow
and must not each need a migration.

Revision ID: a1c2e3d4f5b6
Revises: f8b9c0d1e2a3
Create Date: 2026-07-10 00:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1c2e3d4f5b6"
down_revision: str | None = "f8b9c0d1e2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lineage_edges",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("upstream_asset_id", UUID(as_uuid=True), nullable=False),
        sa.Column("downstream_asset_id", UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("connection_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "first_seen",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_seen",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["upstream_asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["downstream_asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["connection_id"], ["connections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "upstream_asset_id",
            "downstream_asset_id",
            "source",
            "connection_id",
            name="uq_lineage_edges_up_down_source_conn",
        ),
    )
    op.create_index("ix_lineage_edges_upstream", "lineage_edges", ["upstream_asset_id"])
    op.create_index("ix_lineage_edges_downstream", "lineage_edges", ["downstream_asset_id"])


def downgrade() -> None:
    op.drop_index("ix_lineage_edges_downstream", table_name="lineage_edges")
    op.drop_index("ix_lineage_edges_upstream", table_name="lineage_edges")
    op.drop_table("lineage_edges")
