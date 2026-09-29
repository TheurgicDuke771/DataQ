"""check_suggestions: the automatic-coverage review queue (ADR 0047 §4)

A new table, so additive and deployable ahead of the code that writes it: proposed rules per
suite, keyed by ``(suite_id, fingerprint)`` so a rejected rule is never proposed again.

Rollback: downgrade drops the table and with it every pending, accepted and rejected
suggestion (accepted ones already exist as checks; only the rejection memory is lost).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bf15e3a01a5f"
down_revision: str | None = "0985227a6ccf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "check_suggestions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "suite_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("suites.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("expectation_type", sa.String(128), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column(
            "check_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("checks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "decided_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "source IN ('profile', 'llm')",
            name=op.f("ck_check_suggestions_suggestion_source_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected')",
            name=op.f("ck_check_suggestions_suggestion_status_valid"),
        ),
        sa.UniqueConstraint("suite_id", "fingerprint", name=op.f("uq_check_suggestions_rule")),
    )
    op.create_index(
        "ix_check_suggestions_suite_status", "check_suggestions", ["suite_id", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_check_suggestions_suite_status", table_name="check_suggestions")
    op.drop_table("check_suggestions")
