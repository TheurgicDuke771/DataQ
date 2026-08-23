"""widen results.status CHECK with operational skip + error (#122)"""

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
    # Narrowing back to the four tiers is lossy for operational rows: map 'error' -> 'fail' (could
    # not evaluate) and 'skip' -> 'pass' (no penalty) so the narrowed constraint applies cleanly.
    op.execute("UPDATE results SET status = 'fail' WHERE status = 'error'")
    op.execute("UPDATE results SET status = 'pass' WHERE status = 'skip'")
    op.drop_constraint("status_valid", "results", type_="check")
    op.create_check_constraint(
        "status_valid",
        "results",
        "status IN ('pass', 'warn', 'fail', 'critical')",
    )
