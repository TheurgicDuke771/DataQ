"""Add column-level lineage to lineage_edges (#901).

Revision ID: a1b2c3d4e5f8
Revises: 960d18679639
Create Date: 2026-07-18

Column mappings belong to the table→table edge they refine, so they ride the edge
row as a JSONB list of ``[upstream_column, downstream_column]`` pairs rather than a
fourth lineage table — the upsert/prune/provenance regime stays exactly the one
``lineage_edges`` already has, and a future column-grain blast radius can promote
them to their own table without losing anything (the pairs are all it would need).

**Backward-compatible by construction** (CLAUDE.md §6): one nullable additive
column. NULL means "no column pairs ever observed for this edge" (dbt, Snowflake
OBJECT_DEPENDENCIES — or a UC edge whose queries predate the pull window); the
write path only ever records observed pairs, merged union-wise across refreshes.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "a1b2c3d4e5f8"
down_revision = "960d18679639"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("lineage_edges", sa.Column("columns", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("lineage_edges", "columns")
