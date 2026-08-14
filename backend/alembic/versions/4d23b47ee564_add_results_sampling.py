"""add results.sampling — how much of the dataset a check actually saw (#595)

Scale-aware execution (G-b) lets a suite's run target declare a row cap, so the
flat-file and Unity Catalog runners can validate a bounded *sample* instead of
materialising a whole multi-million-row dataset in the worker. A verdict reached
on a sample is a weaker claim than one reached on everything, and the issue's
acceptance criterion is explicit about it: "a check that passed on a sample must
say so".

`sampling` is that record — a small JSONB document written by the runner:

    {"strategy": "random", "requested_rows": 100000, "rows": 100000,
     "total_rows": 5000000, "sampled": true, "seed": 7}

Per-RESULT, not per-run, because within one run different checks legitimately
see different amounts: a volume monitor's `COUNT(*)` pushes down and is exact,
and a Unity Catalog custom-SQL check evaluates against a SQL batch over the whole
table, while the expectations beside them ran on the sample. A run-level flag
would have to lie about one of them.

`sampled` is stored explicitly rather than derived from `rows < total_rows`
because `total_rows` is legitimately NULL for a head sample that stopped reading
early — and because a "sample" that covered the whole dataset is not a sample,
so labelling it one would cry wolf on every small target.

Rollback plan: `downgrade()` drops the column. Nothing reads it outside the run
path and the run-detail read model, and both treat NULL as "no sampling record"
— which is also what every row written before this migration means — so a
rollback loses only the annotation, never a result.

Purely additive and backward-compatible (CLAUDE.md migration rules): one
nullable column on `results`, no backfill, no other table touched, no rewrite
(Postgres adds a nullable column with no default as a catalog-only change, so
there is no table lock beyond a brief ACCESS EXCLUSIVE for the catalog update).
Code deployed before this migration keeps working unmodified — it never reads or
writes the column.

Re-parented onto `fbf4fe92e295` (the #323 retention-sweep index) rather than the
`5ffa2405f9e8` it was written against: that revision landed on `main` while this
branch was open and claimed the same parent, which makes two alembic heads and
fails every `upgrade head`. The two migrations are independent (an index on
`results` vs a nullable column on `results`), so serialising them is a
re-parenting, not a rebase of behaviour.

Revision ID: 4d23b47ee564
Revises: fbf4fe92e295
Create Date: 2026-08-13 00:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4d23b47ee564"
down_revision: str | None = "fbf4fe92e295"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("results", sa.Column("sampling", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("results", "sampling")
