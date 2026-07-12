"""add checks.source_connection_id — the comparison source ref (ADR 0015)

ADR 0015 (two-connection comparison check model): the suite stays
single-connection and supplies the target under test; a `comparison` check adds
exactly one source (baseline) ref. This lands that ref:

* `checks.source_connection_id` — nullable FK → connections, **ON DELETE
  RESTRICT** (the service pre-checks and 409s; the FK is the backstop), indexed.
* CHECK `comparison_source_presence`: presence ⇔ `kind = 'comparison'` — every
  existing row is non-comparison with NULL, so the constraint validates
  instantly with no backfill.
* `check_versions.source_connection_id` — plain UUID snapshot column, **no FK**
  (ADR 0020 self-containment: history must never block deleting a connection an
  old version once pointed at).

Additive & backward-compatible (CLAUDE.md migration rules): nullable columns,
no defaults, no backfill; deployed code that predates the comparison authoring
path never writes either column.

Revision ID: b7c8d9e0f1a2
Revises: 1a2b3c4d5e6f
Create Date: 2026-07-12 00:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c8d9e0f1a2"
down_revision: str | None = "1a2b3c4d5e6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "checks",
        sa.Column("source_connection_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_checks_source_connection_id_connections"),
        "checks",
        "connections",
        ["source_connection_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_checks_comparison_source_presence"),
        "checks",
        "(kind = 'comparison') = (source_connection_id IS NOT NULL)",
    )
    op.create_index("ix_checks_source_connection_id", "checks", ["source_connection_id"])
    op.add_column(
        "check_versions",
        sa.Column("source_connection_id", postgresql.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("check_versions", "source_connection_id")
    op.drop_index("ix_checks_source_connection_id", table_name="checks")
    op.drop_constraint(op.f("ck_checks_comparison_source_presence"), "checks", type_="check")
    op.drop_constraint(
        op.f("fk_checks_source_connection_id_connections"), "checks", type_="foreignkey"
    )
    op.drop_column("checks", "source_connection_id")
