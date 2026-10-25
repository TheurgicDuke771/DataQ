"""connection_versions / check_versions: redacted_at + redacted_by (#685)

Additive: two nullable columns on each snapshot table, both NULL for every existing row (nothing
has been redacted). A redaction sets them and empties the row's `config`; the flag lives in a
column so a user-supplied config can never masquerade as a tombstone. `redacted_by` is a plain FK
to users with SET NULL, like `changed_by` — the redaction outlives its actor.

Rollback: downgrade drops the four columns; the emptied configs stay empty (the values were
removed on purpose and are not restorable).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "878cff0aebbe"
down_revision: str | None = "7a1c5d9e2b44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("connection_versions", "check_versions")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("redacted_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column(
            table,
            sa.Column(
                "redacted_by",
                UUID(as_uuid=True),
                # The metadata naming convention, spelled out: add_column has no table metadata.
                sa.ForeignKey(
                    "users.id", ondelete="SET NULL", name=f"fk_{table}_redacted_by_users"
                ),
                nullable=True,
            ),
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "redacted_by")
        op.drop_column(table, "redacted_at")
