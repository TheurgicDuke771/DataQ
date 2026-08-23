"""Add monitor_baselines — the reference state stateful monitor kinds diff against (#592)."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "e2f3a4b5c6d7"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None

# Mirrors db.models.CHECK_KINDS at migration time (frozen copy — a migration must
# never import live application code).
_CHECK_KINDS = ("expectation", "freshness", "volume", "schema_drift", "anomaly", "comparison")


def upgrade() -> None:
    op.create_table(
        "monitor_baselines",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "check_id",
            UUID(as_uuid=True),
            sa.ForeignKey("checks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("baseline", JSONB, nullable=False),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "captured_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("check_id", name="uq_monitor_baselines_check"),
        sa.CheckConstraint(
            "kind IN (" + ", ".join(f"'{k}'" for k in _CHECK_KINDS) + ")",
            name="ck_monitor_baselines_kind_valid",
        ),
    )


def downgrade() -> None:
    op.drop_table("monitor_baselines")
