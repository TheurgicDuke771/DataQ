"""Add the DQ-dimension classification column to checks + check_versions (#124).

Revision ID: b1c2d3e4f5a6
Revises: a3b4c5d6e7f8
Create Date: 2026-07-19

ADR 0038. A third classification axis on a check — the *semantic quality aspect*
it measures — orthogonal to `kind` (how the monitor works) and `engine` (what
evaluates it). It is what makes the #889 asset scorecard's coverage view possible
("this asset has no Timeliness checks").

**Backward compatible, no two-step needed.** Both columns are nullable with no
server default, so the currently-deployed code — which never reads or writes
them — keeps working unchanged against this schema. This is pure widening: no
data rewrite, no backfill, no narrowing of an existing column.

**Existing rows are deliberately NOT backfilled** (ADR 0038 §5). A derived value
written by this migration would be indistinguishable from a deliberate user
classification, so a later correction to the derivation map could never tell "the
map said so" from "a human said so". Existing checks surface as unclassified and
are classified on next edit.

`checks.dimension` carries a CHECK constraint over the seven canonical
dimensions; `check_versions.dimension` deliberately does **not** — a snapshot
records what *was*, and history must not become unwritable if the vocabulary
later changes.

Tested up and down locally. Down drops both columns (and the constraint with the
column), which loses any classifications made while the revision was applied —
acceptable for a nullable additive column with no dependent data.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: str | None = "a3b4c5d6e7f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Kept as a literal rather than imported from `models.DQ_DIMENSIONS`: a migration
# must describe the schema at ITS point in history, and pinning it to a live
# module would silently rewrite this revision's meaning when the vocabulary
# changes (ADR 0038 §1 accepts that extending it is a new migration).
_DIMENSIONS = (
    "accuracy",
    "completeness",
    "consistency",
    "integrity",
    "timeliness",
    "uniqueness",
    "validity",
)


def upgrade() -> None:
    op.add_column("checks", sa.Column("dimension", sa.String(length=32), nullable=True))
    op.add_column("check_versions", sa.Column("dimension", sa.String(length=32), nullable=True))
    quoted = ", ".join(f"'{d}'" for d in _DIMENSIONS)
    op.create_check_constraint("dimension_valid", "checks", f"dimension IN ({quoted})")


def downgrade() -> None:
    op.drop_constraint("dimension_valid", "checks", type_="check")
    op.drop_column("check_versions", "dimension")
    op.drop_column("checks", "dimension")
