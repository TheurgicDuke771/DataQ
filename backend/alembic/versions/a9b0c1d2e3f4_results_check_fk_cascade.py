"""suite-delete cascades: results.check_id + runs.suite_id -> ON DELETE CASCADE (#540)

Revision ID: a9b0c1d2e3f4
Revises: e5f6a7b8c9d0
Create Date: 2026-07-02 04:00:00.000000+00:00

Deleting a suite that had ever RUN 500'd: the ORM cascades the suite's checks,
but ``results.check_id`` (and, next in line, ``runs.suite_id``) had **no
``ondelete``**, so the DB rejected the delete with a ForeignKeyViolation.
Found by the Week-7 live smoke cleaning up its temp suites.

CASCADE matches the schema's posture elsewhere (ADR 0020: cascade-delete
accepted; ``results.run_id`` / ``checks.suite_id`` already cascade) and
changes nothing about retention outside the delete path.

**Backward-compatible**: constraint drop + recreate only, no column change;
old code keeps working during a rolling deploy (the fix is a strict widening —
a previously-erroring delete now succeeds). **Tested up + down locally.**
Down restores the original NO ACTION constraints (rows deleted by a cascade
in the interim are gone, as accepted by ADR 0020).

Locking note (migration-safety review): the drop+add pair runs in one
transaction, so the ACCESS EXCLUSIVE lock from DROP CONSTRAINT is held through
ADD CONSTRAINT's FK-validation scan of the referencing table. Accepted for the
current deploy: prod ``results``/``runs`` are days old (thousands of rows —
a milliseconds scan). If this pattern is ever repeated once ``results`` is
large, split it: ``ADD CONSTRAINT … NOT VALID`` (catalog-only) + a separate
non-transactional ``VALIDATE CONSTRAINT`` (SHARE UPDATE EXCLUSIVE — doesn't
block readers/writers).
"""

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
