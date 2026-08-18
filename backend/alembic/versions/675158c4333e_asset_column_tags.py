"""assets: cache warehouse column classifications — G3 (#433)

Two additive columns on `assets`, holding the column tags DataQ reads from the
warehouse (`dataq_classification`, plus Snowflake's own `PRIVACY_CATEGORY`).

**Why on `assets` rather than a new table.** The asset already IS the table-grain
primitive (ADR 0034) and already carries the identity a suite resolves to, so the
lookup at redaction time is one attribute read on a row the read path has anyway.
A `column_tags` table would add a join and a second lifecycle for a map that is
small, whole-value, and always read in full.

**Why on the ASSET rather than on each result.** A tag added *after* a sample was
captured must still mask that sample — a classification is a statement about the
data, not about the moment it was read. Freezing the map onto each result would
make yesterday's rows permanently unmaskable by today's governance, which is the
wrong direction for the one thing this feature exists to improve.

`column_tags_refreshed_at` is what makes the cache honest: it says when the map
was last read from the warehouse, so a stale or never-populated asset is
distinguishable from one whose columns genuinely carry no tags. Both cases are
treated identically by the redactor (no opinion → fall through) — the timestamp
is for the operator, not the code path.

Purely additive and backward-compatible: two nullable columns, no default, no
rewrite. Code deployed before this migration ignores them; code deployed after
treats NULL as "no tags", which is the same behaviour that shipped before G3.

Tested up + down locally. Rollback: `downgrade()` drops both columns. Lossy only
of a cache that repopulates on the next run.

Revision ID: 675158c4333e
Revises: 115c74d4bf26
Create Date: 2026-08-18 00:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "675158c4333e"
down_revision: str | None = "115c74d4bf26"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column("column_tags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "assets",
        sa.Column("column_tags_refreshed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assets", "column_tags_refreshed_at")
    op.drop_column("assets", "column_tags")
