"""llm_settings: the snowflake_cortex provider and its connection reference (#1655)

Adds nullable ``llm_settings.connection_id`` (FK to ``connections``, ``ON DELETE SET NULL``) and
widens ``ck_llm_settings_llm_provider_valid`` with ``snowflake_cortex``. Both are additive: the
running image never writes the new value or column.

Rollback: downgrade drops the column and restores the previous provider set — safe only while
the settings row is not ``snowflake_cortex`` (the re-added CHECK would fail against it).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ad7ba65e63b2"
down_revision: str | None = "0b451979c77d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BEFORE = "'anthropic', 'openai_compatible'"
_AFTER = "'anthropic', 'openai_compatible', 'snowflake_cortex'"


def _set_provider_check(values: str) -> None:
    op.execute(
        "ALTER TABLE llm_settings DROP CONSTRAINT IF EXISTS ck_llm_settings_llm_provider_valid"
    )
    op.execute(
        "ALTER TABLE llm_settings ADD CONSTRAINT ck_llm_settings_llm_provider_valid "
        f"CHECK (provider IN ({values}))"
    )


def upgrade() -> None:
    op.add_column(
        "llm_settings",
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_llm_settings_connection_id_connections"),
        "llm_settings",
        "connections",
        ["connection_id"],
        ["id"],
        ondelete="SET NULL",
    )
    _set_provider_check(_AFTER)


def downgrade() -> None:
    _set_provider_check(_BEFORE)
    op.drop_constraint(
        op.f("fk_llm_settings_connection_id_connections"), "llm_settings", type_="foreignkey"
    )
    op.drop_column("llm_settings", "connection_id")
