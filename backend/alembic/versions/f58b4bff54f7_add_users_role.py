"""users: add role — the coarse workspace axis (admin | member | viewer), ADR 0033 (#740)"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f58b4bff54f7"
down_revision: str | None = "bee3e56e1a5d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Kept as a literal rather than imported from `db.models`: a migration must describe the schema at
# THIS revision, and stay correct when a later revision widens the vocabulary.
_ROLES = ("admin", "member", "viewer")


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "role",
            sa.String(length=16),
            server_default=sa.text("'member'"),
            nullable=False,
        ),
    )
    # `op.f(...)` with the FULL conventional name, matching the repo's other constraint migrations.
    op.create_check_constraint(
        op.f("ck_users_role_valid"),
        "users",
        sa.text("role IN ({})".format(", ".join(f"'{r}'" for r in _ROLES))),
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_users_role_valid"), "users", type_="check")
    op.drop_column("users", "role")
