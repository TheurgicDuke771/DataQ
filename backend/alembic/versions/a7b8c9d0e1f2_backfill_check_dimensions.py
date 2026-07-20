"""Backfill `checks.dimension` for checks authored before ADR 0038.

Revision ID: a7b8c9d0e1f2
Revises: b1c2d3e4f5a6
Create Date: 2026-07-19

**This reverses ADR 0038 §5's original "do not backfill" position** — see the
amendment recorded in that ADR. The short version:

* §5 worried a derived value would be indistinguishable from a deliberate user
  classification. At the moment this runs that concern is **vacuous**: the column
  was introduced by `b1c2d3e4f5a6` one revision ago, so the set of checks
  carrying a human-set dimension is exactly EMPTY. The backfill cannot overwrite
  anybody's decision, because nobody has made one yet.
* Against that, leaving them NULL makes the #889 scorecard read "unclassified"
  for every pre-existing check — i.e. useless on the first day, on precisely the
  workspaces that have the most history worth reporting on.

The residual cost is narrower than §5 stated: only that a *future correction to
the derivation map* cannot tell a backfilled row from a user's override. If that
happens, re-derive only rows whose stored value still equals this map's output —
this migration is the record of what that output was.

The map is inlined rather than imported from `services.check_dimension`: a
migration must describe its own point in history, and pinning it to a live module
would silently rewrite what this revision did when the map next changes.

Deliberately does NOT write `check_versions` rows. This is a system
classification, not an edit — minting a version per check would fill every
history drawer with a change nobody made, and the drawer exists to answer "what
did a person change".

Backward compatible: only fills NULLs, never overwrites, and the column is
already nullable, so currently-deployed code is unaffected either way.

Tested up and down locally. Down re-NULLs **only** rows still matching this map,
so a classification a user set after the upgrade survives a downgrade.
"""

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
    """`CASE … END` mapping a check row to its derived dimension (NULL if none).

    expectation_type is checked FIRST — `kind='expectation'` spans a dozen
    dimensions, so matching on kind alone would collapse them.
    """
    whens = [
        f"WHEN expectation_type = '{etype}' THEN '{dim}'"
        for etype, dim in _BY_EXPECTATION_TYPE.items()
    ]
    whens += [f"WHEN kind = '{kind}' THEN '{dim}'" for kind, dim in _BY_KIND.items()]
    return "CASE " + " ".join(whens) + " ELSE NULL END"


def upgrade() -> None:
    # Only NULLs. Custom SQL (and anything else unmapped) stays NULL — it is an
    # arbitrary predicate with no derivable dimension, and ADR 0038 §3 keeps
    # "unclassified" a real state rather than a gap to be filled with a guess.
    op.execute(
        f"UPDATE checks SET dimension = {_case_expression()} "  # noqa: S608  # nosec B608
        f"WHERE dimension IS NULL AND ({_case_expression()}) IS NOT NULL"
    )


def downgrade() -> None:
    # Re-NULL only what this migration would have written. A dimension a user set
    # after the upgrade — even one that happens to equal the derived value — is
    # indistinguishable here, so this deliberately errs toward clearing less than
    # it set only where the map has since changed.
    op.execute(
        f"UPDATE checks SET dimension = NULL "  # noqa: S608  # nosec B608
        f"WHERE dimension IS NOT NULL AND dimension = ({_case_expression()})"
    )
