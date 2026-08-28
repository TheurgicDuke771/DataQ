"""Null the inert severity thresholds on the distinct-value set relations (#1607).

Their results carry no unexpected_percent, so any stored threshold never fired.
Rows in `check_versions` are nulled too: the values were inert when snapshotted,
and leaving them would block `restore_check_version` under the new gate.
Downgrade is a no-op — the nulled values had no effect to restore.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "5656bbfc1495"
down_revision: str | None = "e9f679612fdc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TYPES = (
    "expect_column_distinct_values_to_be_in_set",
    "expect_column_distinct_values_to_contain_set",
)
_IN = "('" + "', '".join(_TYPES) + "')"


def upgrade() -> None:
    for table in ("checks", "check_versions"):
        op.execute(
            f"UPDATE {table} SET warn_threshold = NULL, fail_threshold = NULL, "  # noqa: S608  # nosec B608
            f"critical_threshold = NULL WHERE expectation_type IN {_IN} AND "
            "(warn_threshold IS NOT NULL OR fail_threshold IS NOT NULL "
            "OR critical_threshold IS NOT NULL)"
        )


def downgrade() -> None:
    pass
