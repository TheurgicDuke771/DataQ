"""add connection_versions (per-connection config history)

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-06-20 00:00:00.000000+00:00

Connections carried no history — ``connection_service.update_connection``
overwrote the row in place, so a prior name/config was unrecoverable. This adds a
``connection_versions`` table holding an immutable snapshot of a connection's
editable, **non-secret** state, written on create and after every successful
name/config update; it backs the connection "version history" view. Mirrors
``check_versions`` — per-connection config history, not the cross-entity audit
log (deferred, #310).

The credential is deliberately **not** snapshotted: the secret lives only in the
SecretStore (referenced by the constant ``conn-<id>`` pointer), so it is never
copied here — a credential rotation records no version.

``version_no`` is a per-connection sequence (unique with ``connection_id``). A
version is cascade-deleted with its connection (history not retained past
deletion — accepted), but survives its author (``changed_by`` is ``SET NULL``).

Backward-compatible: a brand-new table, no change to existing tables, no data
rewrite, no two-step. Existing connections simply have no recorded history until
their next create/update writes one.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6f7a8b9c0d1"
down_revision: str | None = "d5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "connection_versions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("connection_id", UUID(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("env", sa.String(length=16), nullable=False),
        sa.Column("config", JSONB(), nullable=False),
        sa.Column("changed_by", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["connection_id"], ["connections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["changed_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "connection_id", "version_no", name="uq_connection_versions_conn_version"
        ),
    )
    op.create_index(
        "ix_connection_versions_connection_id", "connection_versions", ["connection_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_connection_versions_connection_id", table_name="connection_versions")
    op.drop_table("connection_versions")
