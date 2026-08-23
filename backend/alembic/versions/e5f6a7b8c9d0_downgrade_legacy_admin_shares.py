"""downgrade legacy admin shares to edit (ADR 0027 / #482)"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE shares SET permission = 'edit' WHERE permission = 'admin'")


def downgrade() -> None:
    # Irreversible data migration: which 'edit' rows were once 'admin' is not recorded, and the
    # downgraded users keep a valid 'edit' share, so there is no access state to restore.
    pass
