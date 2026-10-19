"""seasonal_periods — the workspace calendar of declared seasonal periods (ADR 0048 decision 5,
#2440 phase 4)

Additive: one new table, nothing dropped, no existing column changes meaning. The table ships
EMPTY and no code reads it yet, so this migration changes no check's verdict on any
deployment. A check attaches entries by listing their ids in its JSONB config (phase 4's
second PR), so there is no join table.

Rollback: `alembic downgrade -1` drops the index and the table. Tested up, down and up again
against a scratch Postgres database holding rows of both kinds.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b94674780cb8"
down_revision: str | None = "543b75689c8c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# A recurring entry is a month-and-day range, every year (it may wrap the year end); a dated
# one is an explicit start and end. Each kind carries exactly its own columns.
# Every column of the kind is tested for NULL by name: a NULL inside BETWEEN or >= makes the
# whole expression NULL, which a CHECK accepts.
_SHAPE = (
    "(kind = 'recurring' AND start_month BETWEEN 1 AND 12 AND start_day BETWEEN 1 AND 31 "
    "AND end_month BETWEEN 1 AND 12 AND end_day BETWEEN 1 AND 31 "
    "AND start_month IS NOT NULL AND start_day IS NOT NULL "
    "AND end_month IS NOT NULL AND end_day IS NOT NULL "
    "AND start_date IS NULL AND end_date IS NULL AND series IS NULL) "
    "OR (kind = 'dated' AND start_date IS NOT NULL AND end_date IS NOT NULL "
    "AND end_date >= start_date "
    "AND start_month IS NULL AND start_day IS NULL AND end_month IS NULL AND end_day IS NULL)"
)


def upgrade() -> None:
    op.create_table(
        "seasonal_periods",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("start_month", sa.SmallInteger(), nullable=True),
        sa.Column("start_day", sa.SmallInteger(), nullable=True),
        sa.Column("end_month", sa.SmallInteger(), nullable=True),
        sa.Column("end_day", sa.SmallInteger(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        # Dated entries with the same series are occurrences of one period across years.
        sa.Column("series", sa.String(128), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("kind IN ('recurring', 'dated')", name="ck_seasonal_periods_kind_valid"),
        sa.CheckConstraint(_SHAPE, name="ck_seasonal_periods_shape"),
    )
    # Expression index, mirroring `uq_users_email_lower`: Postgres cannot express a unique
    # CONSTRAINT over an expression.
    op.create_index(
        "uq_seasonal_periods_name_lower",
        "seasonal_periods",
        [sa.text("lower(name)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_seasonal_periods_name_lower", table_name="seasonal_periods")
    op.drop_table("seasonal_periods")
