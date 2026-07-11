"""lineage_edges: nullable connection_id + partial uniq for catalog-pull (ADR 0034, #762)

A catalog pull (Marquez, #762) discovers lineage edges that belong to **no**
orchestration connection — unlike a dbt refresh, whose edges are provenance-scoped to
the dbt connection. So this migration relaxes `lineage_edges.connection_id` to
**nullable** (the model docstring already anticipated this: "future non-connection
lineage sources … can revisit the nullability") and adds a **partial** unique index

    UNIQUE (upstream_asset_id, downstream_asset_id, source) WHERE connection_id IS NULL

as the dedup key for connection-less sources. The existing full unique constraint
`uq_lineage_edges_up_down_source_conn` still governs connection-scoped sources (dbt),
where `connection_id` is never NULL — Postgres treats NULLs as distinct in a plain
unique constraint, so without this partial index a Marquez upsert (`connection_id
IS NULL`) would never conflict and would duplicate on every refresh. With it, pulled
edges dedupe on `(upstream, downstream, source)` and their prune is scoped to
`(source, connection_id IS NULL)` — it can never touch a dbt row.

Additive & backward-compatible (CLAUDE.md migration rules): relaxing NOT NULL and
adding an index never breaks existing readers/writers; the dbt refresh path is
untouched (it always sets connection_id).

Revision ID: 1a2b3c4d5e6f
Revises: f0a1b2c3d4e5
Create Date: 2026-07-11 00:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1a2b3c4d5e6f"
# NOTE: chained off the current head at build time (f0a1b2c3d4e5, "add_asset_description").
# If the incidents migration (#775, c4e5a6b7d8f9) merges to main first, re-chain this
# down_revision onto that head before merge (single-head invariant — verify `alembic heads`).
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
