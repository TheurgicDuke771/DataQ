"""check engine seam — ADR 0036 slice 1 (#895)

Additive schema for connection-anchored check engines:

* ``checks.engine`` — WHO evaluates the check (default ``'gx'``), constrained to
  the full ADR vocabulary (``gx``/``dmf``/``dqx``/``dataplex``) so native rows
  need no later migration; which values a save actually accepts is decided by
  the application capability map (`datasources.engines`), never the constraint.
* ``check_versions.engine`` — the snapshot twin, so restore reproduces the
  evaluator and history stays self-contained. Backfilled ``'gx'`` via the server
  default, which is exact: 'gx' was the only evaluator when old snapshots were
  cut. Deliberately unconstrained, like `kind`/`dimension` there — history must
  not become unwritable if the vocabulary changes.
* ``connections.engine_capabilities`` — nullable JSONB for the phase-2 per-engine
  probe result (availability + classified remediation). NULL = never probed.

Purely additive and backward-compatible: code deployed before this migration
ignores all three columns; code deployed after reads ``'gx'`` everywhere, which
is the behaviour that shipped before the seam. Server defaults are kept (not
dropped post-backfill) — like ``checks.kind``, an INSERT from pre-seam code must
keep landing as ``'gx'`` during the two-step deploy window.

Tested up + down locally. Rollback: ``downgrade()`` drops the constraint and the
three columns. Lossy only of engine selections and probe results, which cannot
exist before the feature ships.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "9a9ccf49cc8c"
down_revision = "675158c4333e"
branch_labels = None
depends_on = None

_ENGINES = ("gx", "dmf", "dqx", "dataplex")


def upgrade() -> None:
    op.add_column(
        "checks",
        sa.Column("engine", sa.String(32), nullable=False, server_default=sa.text("'gx'")),
    )
    op.create_check_constraint(
        "engine_valid",
        "checks",
        "engine IN (" + ", ".join(f"'{e}'" for e in _ENGINES) + ")",
    )
    op.add_column(
        "check_versions",
        sa.Column("engine", sa.String(32), nullable=False, server_default=sa.text("'gx'")),
    )
    op.add_column(
        "connections",
        sa.Column("engine_capabilities", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("connections", "engine_capabilities")
    op.drop_column("check_versions", "engine")
    op.drop_constraint("engine_valid", "checks", type_="check")
    op.drop_column("checks", "engine")
