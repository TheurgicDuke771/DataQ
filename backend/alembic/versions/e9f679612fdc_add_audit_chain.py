"""audit hash-chain tamper-evidence — ADR 0041 §9 / #1460

Adds `row_hash`/`prev_hash` to `audit_events` (nullable, NOT backfilled — the
chain starts at the first event written after this ships; see the ADR
addendum for why retroactively hashing pre-existing rows would not add real
evidence), the `audit_chain_state` singleton head-lock row, and
`audit_chain_checkpoints` (one row per retention purge, the documented
explanation for the discontinuity a purge creates at the chain's tail).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e9f679612fdc"
down_revision: str | None = "98f8a7a1e1bf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The `audit_chain_state` singleton row id — matches `audit_chain._STATE_ROW_ID`.
_STATE_ROW_ID = 1


def upgrade() -> None:
    op.add_column("audit_events", sa.Column("prev_hash", sa.String(length=64), nullable=True))
    op.add_column("audit_events", sa.Column("row_hash", sa.String(length=64), nullable=True))

    op.create_table(
        "audit_chain_state",
        # No sequence: `id` is always the literal singleton 1, inserted below.
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=False),
        sa.Column("head_hash", sa.String(length=64), nullable=True),
        # No ForeignKey — same reasoning as `audit_events.entity_id`: the row this
        # points to may be purged.
        sa.Column("head_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_chain_state")),
    )
    assert _STATE_ROW_ID == 1  # the literal below assumes this; keep them in sync
    op.execute(
        "INSERT INTO audit_chain_state (id, head_hash, head_event_id, updated_at) "
        "VALUES (1, NULL, NULL, now())"
    )

    op.create_table(
        "audit_chain_checkpoints",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_count", sa.Integer(), nullable=False),
        sa.Column("last_deleted_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_deleted_row_hash", sa.String(length=64), nullable=True),
        sa.Column("first_surviving_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("anchored", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_chain_checkpoints")),
    )


def downgrade() -> None:
    op.drop_table("audit_chain_checkpoints")
    op.drop_table("audit_chain_state")
    op.drop_column("audit_events", "row_hash")
    op.drop_column("audit_events", "prev_hash")
