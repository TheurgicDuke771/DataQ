"""Convert JSON-null lineage_edges.columns to SQL NULL (#907 data fix)."""

from __future__ import annotations

from alembic import op

revision = "b2c3d4e5f6a9"
down_revision = "a1b2c3d4e5f8"
branch_labels = None
depends_on = None

# (table, column) pairs with a nullable JSONB and at least one Python-None writer.
_TARGETS = (
    ("lineage_edges", "columns"),
    ("suites", "target"),
    ("suites", "column_policy"),
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
