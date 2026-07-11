"""add incidents table + suite auto-resolve toggle (G-d phase 3)

ADR 0034 (gap G-d) decision 4, #761: promote the fire-and-forget alert signal to
a stateful, deduped, evidence-carrying **incident** anchored to
``(asset_id, check_id)``. Lifecycle ``open → acknowledged → resolved``; repeat
failures attach as occurrences instead of new rows; the first passing result for
the pair auto-resolves it (per-suite configurable); reopen = a NEW incident
linked to the prior via ``prior_incident_id``.

Additive & backward-compatible (CLAUDE.md migration rules):
  * a brand-new ``incidents`` table — nothing reads it until the #761 service
    ships, so it is safe to deploy on its own (two-step discipline);
  * one **NOT NULL DEFAULT true** column on ``suite_notifications``
    (``auto_resolve_incidents``) — a default-carrying add is backward-compatible
    (existing rows get ``true``, matching the no-config default), no backfill.

The dedup guarantee — at most one *active* (open|acknowledged) incident per
``(asset_id, check_id)`` — is a **partial unique index**; the lifecycle engine's
``INSERT … ON CONFLICT DO NOTHING`` targets it (the #420 upsert-race discipline).

Revision ID: c4e5a6b7d8f9
Revises: f0a1b2c3d4e5
Create Date: 2026-07-10 00:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e5a6b7d8f9"
down_revision: str | None = "f0a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "incidents",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("asset_id", UUID(as_uuid=True), nullable=False),
        sa.Column("check_id", UUID(as_uuid=True), nullable=False),
        sa.Column("suite_id", UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'open'"), nullable=False),
        sa.Column("resolved_by", sa.String(length=16), nullable=True),
        sa.Column("occurrence_count", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", UUID(as_uuid=True), nullable=True),
        sa.Column("acknowledge_note", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("prior_incident_id", UUID(as_uuid=True), nullable=True),
        sa.Column("evidence", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('open', 'acknowledged', 'resolved')", name="incident_status_valid"
        ),
        sa.CheckConstraint(
            "resolved_by IS NULL OR resolved_by IN ('user', 'auto')",
            name="incident_resolved_by_valid",
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["check_id"], ["checks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["suite_id"], ["suites.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["acknowledged_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolved_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["prior_incident_id"], ["incidents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_incidents_asset_id", "incidents", ["asset_id"])
    op.create_index("ix_incidents_check_id", "incidents", ["check_id"])
    op.create_index("ix_incidents_suite_id", "incidents", ["suite_id"])
    op.create_index("ix_incidents_status", "incidents", ["status"])
    # Dedup: at most one active (open|acknowledged) incident per (asset, check).
    op.create_index(
        "uq_incidents_active_asset_check",
        "incidents",
        ["asset_id", "check_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('open', 'acknowledged')"),
    )

    # Per-suite auto-resolve toggle (default on — matches the no-config default).
    op.add_column(
        "suite_notifications",
        sa.Column(
            "auto_resolve_incidents",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("suite_notifications", "auto_resolve_incidents")

    op.drop_index("uq_incidents_active_asset_check", table_name="incidents")
    op.drop_index("ix_incidents_status", table_name="incidents")
    op.drop_index("ix_incidents_suite_id", table_name="incidents")
    op.drop_index("ix_incidents_check_id", table_name="incidents")
    op.drop_index("ix_incidents_asset_id", table_name="incidents")
    op.drop_table("incidents")
