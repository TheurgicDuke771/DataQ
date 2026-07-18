"""Convert JSON-null lineage_edges.columns to SQL NULL (#907 data fix).

Revision ID: b2c3d4e5f6a9
Revises: a1b2c3d4e5f8
Create Date: 2026-07-18

The #903 bulk upsert serialized Python ``None`` as JSON ``null`` (SQLAlchemy JSONB
without ``none_as_null``), which is not SQL NULL — it passes ``IS NOT NULL`` filters
and 500'd every asset page whose neighbourhood touched such an edge (the first prod
Snowflake refresh wrote 339 of them). And the mechanism is not lineage-specific:
every nullable JSONB column with a Python-None writer stores the same landmine
(``suites.target`` provably — suite_service binds an explicit ``None`` and
connection_service filters ``target.isnot(None)``). The ORM types now all carry
``none_as_null=True``; this backfills the rows written before the fix, table by
table. Idempotent, backward-compatible (the value means the same thing on both
sides: "absent"), no-op on a fresh database. Downgrade is a deliberate no-op —
re-manufacturing the defect would be the only thing it could do.

Deploy-window caveat (review finding): the migrate job runs BEFORE the worker
rolls, so an old-image refresh firing inside that window can write fresh JSON
nulls after this UPDATE ran — and the accretive ``COALESCE`` upsert then keeps
them. The readers therefore filter ``jsonb_typeof(...) != 'null'`` (SQL-side)
and guard in Python — this backfill is cleanup, not the safety boundary.
"""

from __future__ import annotations

from alembic import op

revision = "b2c3d4e5f6a9"
down_revision = "a1b2c3d4e5f8"
branch_labels = None
depends_on = None

# (table, column) pairs with a nullable JSONB and at least one Python-None writer.
# `results` rows are matched-row-locked only (an UPDATE, not an ALTER — the #605
# hot-table concern is table-level locks); at this deployment's scale the sweep is
# momentary and the migrate job runs before workers roll.
_TARGETS = (
    ("lineage_edges", "columns"),
    ("suites", "target"),
    ("checks", "column_policy"),
    ("results", "observed_value"),
    ("results", "expected_value"),
    ("results", "sample_failures"),
    ("incidents", "evidence"),
)


def upgrade() -> None:
    for table, column in _TARGETS:
        # Identifiers come only from the module-level literal tuple above — no
        # user input reaches the f-string.
        sql = f"UPDATE {table} SET {column} = NULL WHERE {column} = 'null'::jsonb"  # noqa: S608
        op.execute(sql)


def downgrade() -> None:
    pass
