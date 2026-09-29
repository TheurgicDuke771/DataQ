"""widen the check-kind set for the aggregate monitor kind (#1602)

Adds ``aggregate`` to ``ck_checks_kind_valid`` and ``ck_monitor_baselines_kind_valid`` (both
mirror ``db.models.CHECK_KINDS``). Widening a CHECK is backward compatible: the running image
never writes the new value, so it deploys ahead of (or with) the code.

Rollback: downgrade restores the previous set — safe only while no ``aggregate`` check exists
(the re-added CHECK would fail validation against one). Delete those checks first.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "18f2ee7908cf"
down_revision: str | None = "a43988349c40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BEFORE = "'expectation', 'freshness', 'volume', 'schema_drift', 'anomaly', 'comparison'"
_AFTER = (
    "'expectation', 'freshness', 'volume', 'schema_drift', 'anomaly', 'comparison', 'aggregate'"
)
_TABLES = ("checks", "monitor_baselines")


def _set_kind_check(values: str) -> None:
    for table in _TABLES:
        name = f"ck_{table}_kind_valid"
        # IF EXISTS on the drop so a partial retry after an aborted run re-applies cleanly.
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK (kind IN ({values}))")


def upgrade() -> None:
    _set_kind_check(_AFTER)


def downgrade() -> None:
    _set_kind_check(_BEFORE)
