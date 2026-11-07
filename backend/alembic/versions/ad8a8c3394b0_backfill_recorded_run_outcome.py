"""Backfill the recorded outcome of finished runs (#2348)

From this release a run records its outcome as it succeeds (`a3cda0b90219` added the columns).
This records one for every succeeded run that has none, from the result rows it has now, so a
check deleted from here on no longer rewrites those runs either. A result already deleted with its
check before this ran is gone, and is not counted.

Data only, and idempotent: it touches only succeeded runs whose `recorded_results_total` is NULL.

Rollback: `alembic downgrade -1` is a no-op. A backfilled outcome cannot be told apart from one a
run recorded itself, and either is a true record of the rows at the time, so nothing is undone.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "ad8a8c3394b0"
down_revision: str | None = "a3cda0b90219"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copies of the run-outcome rules at this revision: `checks_total` counts the severity
# tiers (skip and error are operational), `worst_severity` is the highest failing tier.
_BACKFILL = """
UPDATE runs AS r SET
    recorded_results_total = COALESCE(a.results, 0),
    recorded_checks_total = COALESCE(a.checks, 0),
    recorded_checks_passed = COALESCE(a.passed, 0),
    recorded_worst_severity = a.worst
FROM runs AS target
LEFT JOIN (
    SELECT
        run_id,
        count(*) AS results,
        count(*) FILTER (WHERE status IN ('pass', 'warn', 'fail', 'critical')) AS checks,
        count(*) FILTER (WHERE status = 'pass') AS passed,
        CASE
            WHEN bool_or(status = 'critical') THEN 'critical'
            WHEN bool_or(status = 'fail') THEN 'fail'
            WHEN bool_or(status = 'warn') THEN 'warn'
        END AS worst
    FROM results
    WHERE run_id IN (
        SELECT id FROM runs WHERE status = 'succeeded' AND recorded_results_total IS NULL
    )
    GROUP BY run_id
) AS a ON a.run_id = target.id
WHERE r.id = target.id
  AND r.status = 'succeeded'
  AND r.recorded_results_total IS NULL
"""


def upgrade() -> None:
    op.execute(_BACKFILL)


def downgrade() -> None:
    pass
