"""add users.oidc_issuer (provider-neutral auth, ADR 0026 amendment)"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bee3e56e1a5d"
down_revision: str | None = "4d23b47ee564"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("oidc_issuer", sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "oidc_issuer")
