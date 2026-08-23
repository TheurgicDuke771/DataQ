"""orchestrator (type, env) partial unique index (#72)"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "aa33d80c2158"
down_revision: str | None = "cf42d364f74b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "uq_connections_orchestrator_type_env"


def upgrade() -> None:
    op.create_index(
        _INDEX_NAME,
        "connections",
        ["type", "env"],
        unique=True,
        postgresql_where=sa.text("type IN ('adf', 'airflow')"),
    )


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="connections")
