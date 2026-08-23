"""widen connection type-set for the native iceberg datasource"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e716a1b2c3d4"
down_revision: str | None = "c605d1e2f3a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONNECTION_TYPES_WITH_ICEBERG = (
    "'snowflake', 'adls_gen2', 's3', 'unity_catalog', 'iceberg', 'adf', 'airflow', 'dbt'"
)
_CONNECTION_TYPES_NO_ICEBERG = (
    "'snowflake', 'adls_gen2', 's3', 'unity_catalog', 'adf', 'airflow', 'dbt'"
)


def _set_type_check(values: str) -> None:
    # IF EXISTS on the drop so a partial-retry after an aborted run re-applies cleanly.
    op.execute("ALTER TABLE connections DROP CONSTRAINT IF EXISTS ck_connections_type_valid")
    op.execute(
        "ALTER TABLE connections ADD CONSTRAINT ck_connections_type_valid "
        f"CHECK (type IN ({values}))"
    )


def upgrade() -> None:
    _set_type_check(_CONNECTION_TYPES_WITH_ICEBERG)


def downgrade() -> None:
    # Safe only before any iceberg connection row exists (this PR ships the adapter unflagged, so
    # that window closes at first iceberg connection).
    _set_type_check(_CONNECTION_TYPES_NO_ICEBERG)
