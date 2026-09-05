"""workspace_members — in-app workspace membership (ADR 0043 step 1, #1693)

Additive: one new table plus its unique index. Nothing is dropped, no existing
column changes meaning, and no code reads the table yet — the resolver checks
and the admin endpoints ship in a separate PR (CLAUDE.md §6 two-step rule).

The table ships EMPTY on purpose. Membership enforcement is keyed on the table
being non-empty (ADR 0043 decision 3), so this migration changes no behaviour on
any deployment; the switch is an admin writing the first row later.

Rollback: `alembic downgrade -1` drops the table and its index. Tested up, down
and up again against a scratch Postgres database.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0451ebdc77f3"
down_revision: str | None = "c2f5dc250791"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_members",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column(
            "initial_role", sa.String(16), nullable=False, server_default=sa.text("'member'")
        ),
        sa.Column("source", sa.String(16), nullable=False, server_default=sa.text("'admin'")),
        sa.Column(
            "invited_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "initial_role IN ('admin', 'member', 'viewer')",
            name="workspace_member_initial_role_valid",
        ),
        sa.CheckConstraint(
            "source IN ('admin', 'auto_import')", name="workspace_member_source_valid"
        ),
    )
    # Expression index, mirroring `uq_users_email_lower`: Postgres cannot express a
    # unique CONSTRAINT over an expression.
    op.create_index(
        "uq_workspace_members_email_lower",
        "workspace_members",
        [sa.text("lower(email)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_workspace_members_email_lower", table_name="workspace_members")
    op.drop_table("workspace_members")
