"""suite-delete cascades: results.check_id + runs.suite_id -> ON DELETE CASCADE (#540)"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9b0c1d2e3f4"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (constraint name, table, referred table, column)
_FKS = [
    ("fk_results_check_id_checks", "results", "checks", "check_id"),
    ("fk_runs_suite_id_suites", "runs", "suites", "suite_id"),
]


def upgrade() -> None:
    for name, table, referred, column in _FKS:
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(name, table, referred, [column], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    for name, table, referred, column in _FKS:
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(name, table, referred, [column], ["id"])
