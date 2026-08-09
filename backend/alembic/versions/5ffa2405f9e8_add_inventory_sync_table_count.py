"""add connection inventory-sync table-count + zero-drop state (#1242)

`sync_connection_inventory` returning `0` and the sweep recording that tick as
healthy (`inventory_sync_last_error = NULL`, per #1104) is correct for a
genuinely empty database and WRONG for the scenario #1104 was filed about:
Snowflake's `INFORMATION_SCHEMA` is privilege-filtered, not access-denied, so a
role with no grants on the objects gets an empty result set, not an error.
Toggle on, connection test green, zero assets ever appear, and — because zero
rows is not an exception — nothing in the #1104 state says why. Unity Catalog
raises on a missing grant (already covered); this is the Snowflake half.

An empty-by-design database enumerating zero tables is legitimate, so this
can't just become another `inventory_sync_last_error` — that would be a false
alarm in the other direction. Two new nullable columns instead:

* `inventory_sync_last_table_count` — the row count from the last SUCCESSFUL
  sync (mirrors the existing `_last_error`/`_last_attempted_at` pair: stamped
  only on success, since a failed attempt has no count to report). NULL means
  never successfully synced, which is what makes "synced, 0 tables visible"
  distinguishable from "never synced" (NULL) and from "synced, N>0" (>0) — the
  first AC.

* `inventory_sync_zero_since` — set the moment the count transitions from a
  previously-recorded N>0 down to 0 (the privilege-loss/dropped-database
  signal), left untouched while it stays at 0, and cleared back to NULL the
  moment the count is >0 again. A database that has ALWAYS enumerated zero
  never sets this column, so it stays a neutral, non-alarming state — exactly
  the ADR 0040 "confident wrong answer in the other direction" this migration
  exists to avoid. Mirrors the `inventory_sync_failing_since` streak pattern
  already on this table.

Purely additive and backward-compatible (CLAUDE.md migration rules): two
nullable columns on `connections`, no backfill, no other table touched. Code
deployed before this migration keeps working unmodified (it never reads or
writes these columns); the sweep populates them going forward.

Revision ID: 5ffa2405f9e8
Revises: c22fa93eb834
Create Date: 2026-08-09 00:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5ffa2405f9e8"
down_revision: str | None = "c22fa93eb834"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "connections",
        sa.Column("inventory_sync_last_table_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "connections",
        sa.Column("inventory_sync_zero_since", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("connections", "inventory_sync_zero_since")
    op.drop_column("connections", "inventory_sync_last_table_count")
