"""add assets.description (asset-metadata mutation, G-d phase 2)

ADR 0034 (gap G-d) §4: asset-metadata mutation (owner + description) is
workspace-Admin-only, surfaced by the read-only asset view (#760). The asset
already carries `owner_user_id` (the later incident-routing hop); this adds the
free-text `description` the same PATCH endpoint sets.

Additive & backward-compatible (CLAUDE.md migration rules): a single **nullable**
Text column, no default, no backfill. Nothing reads it until the #760 code ships,
so this migration is safe to deploy on its own (two-step discipline).

Revision ID: f0a1b2c3d4e5
Revises: a1c2e3d4f5b6
Create Date: 2026-07-10 00:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f0a1b2c3d4e5"
down_revision: str | None = "a1c2e3d4f5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("assets", sa.Column("description", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("assets", "description")
