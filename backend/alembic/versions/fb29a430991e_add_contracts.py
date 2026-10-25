"""data contracts (ADR 0052, #1977): `contracts`, the suite/check links, `origin = 'contract'`

Additive: a new table, two nullable columns on `suites`, one on `checks`, and the
`checks.origin` CHECK widened by one value. Nothing existing changes shape or default, so the
running release is unaffected and the migration can deploy ahead of the code that reads it.

Rollback: ``downgrade`` turns contract-derived checks into ordinary ones (``origin = 'user'``),
narrows the CHECK, and drops the columns and the table (contract rows and the suite links are
lost; suites, checks and results are untouched).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fb29a430991e"
down_revision: str | None = "878cff0aebbe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ORIGIN_CHECK = "ck_checks_check_origin_valid"
_WITH_CONTRACT = "'user', 'auto', 'suggestion', 'contract'"
_NO_CONTRACT = "'user', 'auto', 'suggestion'"


def _set_origin_check(values: str) -> None:
    op.execute(f"ALTER TABLE checks DROP CONSTRAINT IF EXISTS {_ORIGIN_CHECK}")
    op.execute(f"ALTER TABLE checks ADD CONSTRAINT {_ORIGIN_CHECK} CHECK (origin IN ({values}))")


def upgrade() -> None:
    op.create_table(
        "contracts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("odcs_id", sa.String(length=256), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("domain", sa.String(length=256), nullable=True),
        sa.Column("tenant", sa.String(length=256), nullable=True),
        sa.Column("api_version", sa.String(length=16), nullable=False),
        sa.Column("spec", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "unmapped",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_contracts_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contracts")),
        sa.UniqueConstraint("odcs_id", "version", name=op.f("uq_contracts_odcs_id_version")),
    )
    op.create_index(op.f("ix_contracts_created_by"), "contracts", ["created_by"], unique=False)

    op.add_column("suites", sa.Column("contract_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("suites", sa.Column("contract_object", sa.String(length=256), nullable=True))
    op.create_foreign_key(
        op.f("fk_suites_contract_id_contracts"),
        "suites",
        "contracts",
        ["contract_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_suites_contract_id"), "suites", ["contract_id"], unique=False)

    op.add_column(
        "checks",
        sa.Column("contract_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    _set_origin_check(_WITH_CONTRACT)


def downgrade() -> None:
    # A contract-derived check stays a check: it becomes an ordinary one (its contract link goes
    # with the columns below), so the narrowed CHECK holds and no check or result is lost.
    op.execute("UPDATE checks SET origin = 'user' WHERE origin = 'contract'")
    _set_origin_check(_NO_CONTRACT)
    op.drop_column("checks", "contract_ref")
    op.drop_index(op.f("ix_suites_contract_id"), table_name="suites")
    op.drop_constraint(op.f("fk_suites_contract_id_contracts"), "suites", type_="foreignkey")
    op.drop_column("suites", "contract_object")
    op.drop_column("suites", "contract_id")
    op.drop_index(op.f("ix_contracts_created_by"), table_name="contracts")
    op.drop_table("contracts")
