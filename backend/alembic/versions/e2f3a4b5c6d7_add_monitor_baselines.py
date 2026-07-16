"""Add monitor_baselines — the reference state stateful monitor kinds diff against (#592).

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-07-14

The ``schema_drift`` monitor kind (ADR 0012's first stateful kind) needs somewhere to
persist the column-name/type snapshot each run diffs the live schema against. Designed
for TWO consumers (the #592 AC): the W5 ``anomaly`` kind (#593) stores its metric
baseline parameters in the same shape, so the payload is a kind-shaped JSONB —
one persistence shape, no second table later.

Semantics:
- One CURRENT baseline per check (``UNIQUE (check_id)``) — a re-baseline REPLACES the
  row; historical drift observations live in ``results``, not here.
- ``kind`` is denormalized from the check (queryability), CHECK-constrained to the
  shared kind vocabulary; the check's own kind is the authority.
- ``captured_by`` is the manual re-baseline actor (SET NULL on user delete);
  NULL = the run path captured it automatically on the check's first run.
- Cascade-deleted with the check. Shape metadata only — never row data, so it is
  outside the PII retention sweep.

**Backward-compatible by construction** (CLAUDE.md §6): a brand-new table nothing
deployed reads or writes; the code that uses it ships after this migration is applied.

Down-migration drops the table; nothing else is touched.
"""

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
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
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
