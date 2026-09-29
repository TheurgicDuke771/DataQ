"""coverage-loop schema (ADR 0047): suite/check origin, asset exclusion, incident resolution

Adds, all additive:

- ``suites.origin`` (``'user'`` default, CHECK ``user|auto``) and ``suites.auto_state`` (JSONB,
  NULL), plus the partial unique index ``uq_suites_auto_per_asset`` — one automatic suite per
  asset;
- ``checks.origin`` (``'user'`` default, CHECK ``user|auto|suggestion``);
- ``assets.auto_coverage_excluded`` (``false`` default);
- ``incidents.resolution`` (NULL, CHECK ``fixed|expected_change|false_positive``).

Every new NOT NULL column has a constant default, so PostgreSQL adds it without a table
rewrite, and the running image never writes these columns, so this deploys ahead of the code.

Rollback: downgrade drops the index, constraints and columns. Safe while no automatic suite
exists; afterwards it discards which suites and checks the coverage loop owns.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0985227a6ccf"
down_revision: str | None = "18f2ee7908cf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "suites",
        sa.Column("origin", sa.String(16), nullable=False, server_default=sa.text("'user'")),
    )
    op.add_column(
        "suites", sa.Column("auto_state", postgresql.JSONB(none_as_null=True), nullable=True)
    )
    op.create_check_constraint(
        op.f("ck_suites_suite_origin_valid"), "suites", "origin IN ('user', 'auto')"
    )
    op.create_index(
        "uq_suites_auto_per_asset",
        "suites",
        ["asset_id"],
        unique=True,
        postgresql_where=sa.text("origin = 'auto'"),
    )
    op.add_column(
        "checks",
        sa.Column("origin", sa.String(16), nullable=False, server_default=sa.text("'user'")),
    )
    op.create_check_constraint(
        op.f("ck_checks_check_origin_valid"), "checks", "origin IN ('user', 'auto', 'suggestion')"
    )
    op.add_column(
        "assets",
        sa.Column(
            "auto_coverage_excluded", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    op.add_column("incidents", sa.Column("resolution", sa.String(32), nullable=True))
    op.create_check_constraint(
        op.f("ck_incidents_incident_resolution_valid"),
        "incidents",
        "resolution IN ('fixed', 'expected_change', 'false_positive')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_incidents_incident_resolution_valid"), "incidents", type_="check")
    op.drop_column("incidents", "resolution")
    op.drop_column("assets", "auto_coverage_excluded")
    op.drop_constraint(op.f("ck_checks_check_origin_valid"), "checks", type_="check")
    op.drop_column("checks", "origin")
    op.drop_index("uq_suites_auto_per_asset", table_name="suites")
    op.drop_constraint(op.f("ck_suites_suite_origin_valid"), "suites", type_="check")
    op.drop_column("suites", "auto_state")
    op.drop_column("suites", "origin")
