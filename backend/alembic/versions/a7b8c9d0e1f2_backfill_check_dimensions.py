"""Backfill `checks.dimension` for checks authored before ADR 0038."""

from collections.abc import Sequence

from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# expectation_type -> dimension, as of 2026-07-19 (ADR 0038 §3).
_BY_EXPECTATION_TYPE = {
    "expect_column_values_to_not_be_null": "completeness",
    "expect_table_row_count_to_be_between": "completeness",
    "expect_column_values_to_be_unique": "uniqueness",
    "expect_column_values_to_be_between": "validity",
    "expect_column_values_to_be_in_set": "validity",
    "expect_column_values_to_match_regex": "validity",
    "expect_column_value_lengths_to_be_between": "validity",
    "expect_column_values_to_be_of_type": "validity",
}
# Fallback by kind, for the non-GX kinds whose expectation_type is generated.
_BY_KIND = {
    "freshness": "timeliness",
    "volume": "completeness",
    "schema_drift": "consistency",
    "comparison": "consistency",
}


def _case_expression() -> str:
    """`CASE … END` mapping a check row to its derived dimension (NULL if none)."""
    whens = [
        f"WHEN expectation_type = '{etype}' THEN '{dim}'"
        for etype, dim in _BY_EXPECTATION_TYPE.items()
    ]
    whens += [f"WHEN kind = '{kind}' THEN '{dim}'" for kind, dim in _BY_KIND.items()]
    return "CASE " + " ".join(whens) + " ELSE NULL END"


def upgrade() -> None:
    # Only NULLs.
    op.execute(
        f"UPDATE checks SET dimension = {_case_expression()} "  # noqa: S608  # nosec B608
        f"WHERE dimension IS NULL AND ({_case_expression()}) IS NOT NULL"
    )


def downgrade() -> None:
    # Re-NULL only what this migration would have written.
    op.execute(
        f"UPDATE checks SET dimension = NULL "  # noqa: S608  # nosec B608
        f"WHERE dimension IS NOT NULL AND dimension = ({_case_expression()})"
    )
