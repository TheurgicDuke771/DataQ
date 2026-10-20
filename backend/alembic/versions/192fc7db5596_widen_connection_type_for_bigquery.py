"""widen connection type-set for the Google BigQuery datasource (#1681)

Adds ``bigquery`` to ``ck_connections_type_valid``. Widening a CHECK is backward compatible: the
running image never writes the new value, so it deploys ahead of (or with) the adapter.

Rollback: downgrade restores the previous set — safe only while no ``bigquery`` connection row
exists (the re-added CHECK would fail validation against one). Delete those rows first.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "192fc7db5596"
down_revision: str | None = "094076e0e7c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BEFORE = (
    "'snowflake', 'adls_gen2', 's3', 'gcs', 'unity_catalog', 'iceberg', 'postgres', 'mysql', "
    "'trino', 'mssql', 'athena', 'redshift', 'adf', 'airflow', 'dbt'"
)
_AFTER = (
    "'snowflake', 'adls_gen2', 's3', 'gcs', 'unity_catalog', 'iceberg', 'postgres', 'mysql', "
    "'trino', 'mssql', 'athena', 'redshift', 'bigquery', 'adf', 'airflow', 'dbt'"
)


def _set_type_check(values: str) -> None:
    op.execute("ALTER TABLE connections DROP CONSTRAINT IF EXISTS ck_connections_type_valid")
    op.execute(
        "ALTER TABLE connections ADD CONSTRAINT ck_connections_type_valid "
        f"CHECK (type IN ({values}))"
    )


def upgrade() -> None:
    _set_type_check(_AFTER)


def downgrade() -> None:
    rows = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM connections WHERE type = 'bigquery'"))
        .scalar_one()
    )
    if rows:
        raise RuntimeError(
            f"{rows} bigquery connection(s) exist; delete them before downgrading past "
            "192fc7db5596 (the narrower type CHECK would reject them)"
        )
    _set_type_check(_BEFORE)
