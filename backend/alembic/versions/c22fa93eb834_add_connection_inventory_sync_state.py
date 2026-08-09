"""add connection inventory-sync outcome state (#1104)

A connection opted into inventory sync (`config.inventory_sync`, ADR 0040) whose
principal cannot read the enumeration query (UC: no SELECT on
`system.information_schema`; Snowflake: revoked INFORMATION_SCHEMA access) fails
every daily tick inside `sync_connection_inventory`. It is caught fail-soft and
logged (`inventory_sync_connection_failed`) — correct for the sweep — but the
USER-facing state was: toggle on, connection test green (the `SELECT 1` probe
never exercises this query), zero assets ever appear, no surface says why. The
#828 shape again.

These three nullable columns mirror the `lineage_last_*` pattern added in
960d18679639 (#858): `inventory_sync_last_attempted_at` stamps every attempt,
`inventory_sync_last_error` holds a CLASSIFIED reason (never raw exception
text — NULL means the last attempt succeeded), and
`inventory_sync_failing_since` marks the start of the current failure streak
(NULL while healthy) so the connection card can say "inventory sync failing
since <ts>: <reason>" instead of nothing at all.

Purely additive and backward-compatible (CLAUDE.md migration rules): three
nullable columns on `connections`, no backfill, no other table touched. Code
deployed before this migration keeps working unmodified (it never reads or
writes these columns); the sweep task populates them going forward.

Revision ID: c22fa93eb834
Revises: 6230293aea96
Create Date: 2026-08-08 00:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c22fa93eb834"
down_revision: str | None = "6230293aea96"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "connections",
        sa.Column("inventory_sync_last_attempted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "connections",
        sa.Column("inventory_sync_last_error", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "connections",
        sa.Column("inventory_sync_failing_since", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("connections", "inventory_sync_failing_since")
    op.drop_column("connections", "inventory_sync_last_error")
    op.drop_column("connections", "inventory_sync_last_attempted_at")
