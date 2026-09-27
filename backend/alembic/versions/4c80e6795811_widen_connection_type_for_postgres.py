"""widen connection type-set for the generic PostgreSQL datasource (#1678)

Adds ``postgres`` to ``ck_connections_type_valid``. Widening a CHECK is backward compatible: the
running image never writes the new value, so it deploys ahead of (or with) the adapter.

Rollback: downgrade restores the previous set — safe only while no ``postgres`` connection row
exists (the re-added CHECK would fail validation against one). Delete those rows first.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4c80e6795811"
down_revision: str | None = "81e4ec4c408b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BEFORE = "'snowflake', 'adls_gen2', 's3', 'unity_catalog', 'iceberg', 'adf', 'airflow', 'dbt'"
_AFTER = (
    "'snowflake', 'adls_gen2', 's3', 'unity_catalog', 'iceberg', 'postgres', 'adf', 'airflow', "
    "'dbt'"
)


def _set_type_check(values: str) -> None:
    # IF EXISTS on the drop so a partial-retry after an aborted run re-applies cleanly.
    op.execute("ALTER TABLE connections DROP CONSTRAINT IF EXISTS ck_connections_type_valid")
    op.execute(
        "ALTER TABLE connections ADD CONSTRAINT ck_connections_type_valid "
        f"CHECK (type IN ({values}))"
    )


def upgrade() -> None:
    _set_type_check(_AFTER)


def downgrade() -> None:
    _set_type_check(_BEFORE)
