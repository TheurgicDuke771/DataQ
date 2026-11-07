"""Recorded run outcome on `runs` (#2348)

A run's `checks_total` / `checks_passed` / `worst_severity` are derived from its result rows, and
`results.check_id` is CASCADE, so deleting a check rewrites every past run it took part in. The
four `recorded_*` columns hold the outcome as it stood when the run finished;
`recorded_results_total` counts every result row (skip and error included), so a reader can tell
how many rows were removed since. The prefix keeps them apart from the derived read-model fields
of the same names.

Additive: four nullable columns and two CHECK constraints that NULL satisfies. NULL means not
recorded (a run still in flight, or one that finished before the code that writes them). No code
reads or writes them yet; that ships in the next PR, after this one is deployed.

Rollback: `alembic downgrade -1` drops the constraints and the columns. Tested up, down and up
again against a scratch Postgres database.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a3cda0b90219"
down_revision: str | None = "ea0a2ff2bf6b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COUNTS_CONSISTENT = (
    "recorded_checks_passed >= 0 AND recorded_checks_passed <= recorded_checks_total"
    " AND recorded_checks_total <= recorded_results_total"
)


def upgrade() -> None:
    op.add_column("runs", sa.Column("recorded_results_total", sa.Integer(), nullable=True))
    op.add_column("runs", sa.Column("recorded_checks_total", sa.Integer(), nullable=True))
    op.add_column("runs", sa.Column("recorded_checks_passed", sa.Integer(), nullable=True))
    op.add_column("runs", sa.Column("recorded_worst_severity", sa.String(16), nullable=True))
    op.create_check_constraint(
        op.f("ck_runs_recorded_worst_severity_valid"),
        "runs",
        "recorded_worst_severity IN ('warn', 'fail', 'critical')",
    )
    op.create_check_constraint(op.f("ck_runs_outcome_counts_valid"), "runs", _COUNTS_CONSISTENT)


def downgrade() -> None:
    op.drop_constraint(op.f("ck_runs_outcome_counts_valid"), "runs", type_="check")
    op.drop_constraint(op.f("ck_runs_recorded_worst_severity_valid"), "runs", type_="check")
    op.drop_column("runs", "recorded_worst_severity")
    op.drop_column("runs", "recorded_checks_passed")
    op.drop_column("runs", "recorded_checks_total")
    op.drop_column("runs", "recorded_results_total")
