"""CHECK (id = 1) on the four singleton settings tables (#2424)

`privacy_settings`, `scoring_settings`, `llm_settings` and `audit_chain_state` are read and
written at `id = 1` only, so they were singletons by convention. A writer that omitted `id`, or a
second row inserted by hand, would sit beside the real one and a `get(Model, 1)` reader would
ignore it. `coverage_settings` and `telemetry_settings` already carry this constraint.

Additive: each table holds at most one row, so each constraint validates instantly. A table that
somehow holds a row with another id stops the migration with the table named, rather than
deleting data. No code change depends on the constraint.

Rollback: `alembic downgrade -1` drops the four constraints. Tested up, down and up again against
a scratch Postgres database.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "ea0a2ff2bf6b"
down_revision: str | None = "fb29a430991e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("privacy_settings", "scoring_settings", "llm_settings", "audit_chain_state")


def upgrade() -> None:
    bind = op.get_bind()
    for table in TABLES:
        rows = sa.table(table, sa.column("id"))
        stray = bind.scalar(sa.select(sa.func.count()).select_from(rows).where(rows.c.id != 1))
        if stray:
            raise RuntimeError(
                f"{table} holds {stray} row(s) with an id other than 1; the services only read "
                "id = 1. Inspect and remove them, then re-run the migration."
            )
        op.create_check_constraint(op.f(f"ck_{table}_singleton"), table, "id = 1")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_constraint(op.f(f"ck_{table}_singleton"), table, type_="check")
