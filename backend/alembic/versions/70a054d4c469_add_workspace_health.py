"""add workspace_health — workspace-level delivered-alert flags (#1052)

The workspace-wide orchestration-poll staleness alert has no `Connection` row to
carry its `health_alerted_at`, so the #843 delivered-first rule needs a
workspace-level home. One row per signal key (`key` PK); `alerted_at` is written
only after a FAILING publish actually succeeds and cleared after RECOVERED, and
the row doubles as the cross-replica claim (`FOR UPDATE SKIP LOCKED`).

Additive & backward-compatible (CLAUDE.md migration rules): a brand-new table,
no existing read path touched. No backfill — an absent row means "no alert
outstanding", which is the correct cold-start state.

Revision ID: 70a054d4c469
Revises: 00a938b64317
Create Date: 2026-07-28 00:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "70a054d4c469"
down_revision: str | None = "00a938b64317"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_health",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("alerted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("workspace_health")
