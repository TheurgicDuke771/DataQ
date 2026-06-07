"""widen results.status CHECK with operational skip + error (#122)

Revision ID: 784847178482
Revises: 9c59b6a44f33
Create Date: 2026-06-07 21:41:49.182246+00:00

Adds two *operational* (non-severity) result statuses orthogonal to the four
ADR 0005 health-score tiers:

- ``skip``  — the check did not evaluate (n/a to the batch, gated off, file missing).
- ``error`` — the check threw / could not be evaluated (distinct from ``fail``,
  which is a successful evaluation that breached an expectation).

Pure CHECK widening: no data rewrite, no two-step. Existing rows already satisfy
the broader constraint. The run path emits these later (Week-5 execution-engine
hardening); this lands the cheap seam now so that work needs no migration. The
health-score aggregate must exclude ``skip``/``error`` from N — they carry no
penalty weight (see ``RESULT_STATUSES`` in ``db/models.py``).
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "784847178482"
down_revision: str | None = "9c59b6a44f33"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("status_valid", "results", type_="check")
    op.create_check_constraint(
        "status_valid",
        "results",
        "status IN ('pass', 'warn', 'fail', 'critical', 'skip', 'error')",
    )


def downgrade() -> None:
    # Narrowing back to the four tiers is lossy for operational rows: map
    # 'error' -> 'fail' (could not evaluate) and 'skip' -> 'pass' (no penalty)
    # so the narrowed constraint applies cleanly.
    op.execute("UPDATE results SET status = 'fail' WHERE status = 'error'")
    op.execute("UPDATE results SET status = 'pass' WHERE status = 'skip'")
    op.drop_constraint("status_valid", "results", type_="check")
    op.create_check_constraint(
        "status_valid",
        "results",
        "status IN ('pass', 'warn', 'fail', 'critical')",
    )
