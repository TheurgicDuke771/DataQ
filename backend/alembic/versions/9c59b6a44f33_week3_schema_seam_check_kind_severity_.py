"""week3 schema seam: check.kind + severity thresholds + metric_value/duration_ms + status tiers

The one-shot Week-3 schema seam (CLAUDE.md §5/§10). Bundles every forward-compat
column the suite/check/result layer needs so v1.x monitors and the Week-6
dashboard don't force a second backward-compat two-step:

- `checks.kind` — monitor-kind discriminator (ADR 0012; `comparison` reserved by
  ADR 0014). v1 only ever writes `'expectation'`; the rest are constraint-valid
  but unused.
- `checks.{warn,fail,critical}_threshold` — optional severity tiers (ADR 0005).
- `results.metric_value` (NUMERIC) + `results.duration_ms` (INT) — the
  SQL-aggregatable scalar + per-check runtime (ADR 0012).
- `results.status` CHECK retargeted from `(passed, failed, skipped)` to the
  severity tiers `(pass, warn, fail, critical)` (ADR 0005).

Backward-compat / migration-safety note: this DB is not yet deployed (deploy is
Week 7; two-step deploy discipline starts W5), so narrowing the `results.status`
CHECK alongside the `run_service` code that writes it is safe in one step. The
status retarget still updates existing rows to the new vocabulary *before*
swapping the CHECK, so the constraint never rejects in-flight data. All other
ops are pure additions (nullable columns / a defaulted NOT NULL with a
server_default backfill) — no existing read path breaks.

Revision ID: 9c59b6a44f33
Revises: aa33d80c2158
Create Date: 2026-06-05 00:57:46.247795+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9c59b6a44f33"
down_revision: str | None = "aa33d80c2158"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── checks: monitor-kind discriminator (ADR 0012/0014) ──
    # NOT NULL with a server_default so existing rows backfill to 'expectation'
    # without a separate data step.
    op.add_column(
        "checks",
        sa.Column("kind", sa.String(32), nullable=False, server_default="expectation"),
    )
    op.create_check_constraint(
        "kind_valid",
        "checks",
        "kind IN ('expectation', 'freshness', 'volume', 'schema_drift', 'anomaly', 'comparison')",
    )

    # ── checks: optional severity thresholds (ADR 0005) ──
    op.add_column("checks", sa.Column("warn_threshold", sa.Numeric(), nullable=True))
    op.add_column("checks", sa.Column("fail_threshold", sa.Numeric(), nullable=True))
    op.add_column("checks", sa.Column("critical_threshold", sa.Numeric(), nullable=True))

    # ── results: SQL-aggregatable metric + per-check runtime (ADR 0012) ──
    op.add_column("results", sa.Column("metric_value", sa.Numeric(), nullable=True))
    op.add_column("results", sa.Column("duration_ms", sa.Integer(), nullable=True))

    # ── results.status: retarget to severity tiers (ADR 0005) ──
    # Update existing rows to the new vocabulary BEFORE swapping the CHECK, so the
    # constraint never has to reject pre-existing data. `skipped` was never
    # emitted by the run path (run_service only wrote passed/failed); coerce any
    # stray rows to 'fail' conservatively (a non-evaluated check is not healthy).
    op.execute("UPDATE results SET status = 'pass' WHERE status = 'passed'")
    op.execute("UPDATE results SET status = 'fail' WHERE status IN ('failed', 'skipped')")
    op.drop_constraint("status_valid", "results", type_="check")
    op.create_check_constraint(
        "status_valid",
        "results",
        "status IN ('pass', 'warn', 'fail', 'critical')",
    )


def downgrade() -> None:
    # Reverse the status retarget. Tier collapse is inherently lossy: warn/fail/
    # critical all fold back to the binary 'failed'.
    op.execute("UPDATE results SET status = 'passed' WHERE status = 'pass'")
    op.execute("UPDATE results SET status = 'failed' WHERE status IN ('warn', 'fail', 'critical')")
    op.drop_constraint("status_valid", "results", type_="check")
    op.create_check_constraint(
        "status_valid",
        "results",
        "status IN ('passed', 'failed', 'skipped')",
    )

    op.drop_column("results", "duration_ms")
    op.drop_column("results", "metric_value")

    op.drop_column("checks", "critical_threshold")
    op.drop_column("checks", "fail_threshold")
    op.drop_column("checks", "warn_threshold")
    op.drop_constraint("kind_valid", "checks", type_="check")
    op.drop_column("checks", "kind")
