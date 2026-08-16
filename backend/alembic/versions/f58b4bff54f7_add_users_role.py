"""users: add role — the coarse workspace axis (admin | member | viewer), ADR 0033 (#740)

Authorization has always been two axes with one axis degenerate. The fine axis
(`shares.permission`, ADR 0027) works; the coarse one was a binary env allowlist
(`WORKSPACE_ADMIN_EMAILS`) with no stored representation at all — recorded as gap
G-e. This column is the coarse axis, stored: `admin | member | viewer`, one
additive column on `users`, no roles table (groups and custom roles are
explicitly deferred in the ADR).

**`server_default 'member'` is the correct historical value, not a placeholder.**
Before this shipped, every authenticated non-allowlisted user could do exactly
what ADR 0033's matrix now calls Member: create suites, receive `edit` shares,
mint PATs, reference connections. So backfilling `member` preserves behavior
row-for-row rather than approximating it. Allowlisted operators are NOT
backfilled to `admin` here on purpose — `core.auth.is_workspace_admin` resolves
`role == 'admin' OR allowlisted`, so they keep admin from the moment this
deploys, and the sign-in paths write `admin` through on their next request. A
backfill would have had to re-read the env allowlist from inside a migration,
where it is neither available nor trustworthy.

Purely additive and backward-compatible (CLAUDE.md migration rules): one NOT NULL
column with a server default (a metadata-only rewrite-free ADD on Postgres 11+),
plus its CHECK. No other table is touched. Code deployed *before* this migration
keeps working unmodified (it never reads the column); code deployed *after* needs
the column, which is why the migrate job runs ahead of the container roll — the
ordering the Deploy workflow already enforces.

Tested up + down locally. Rollback: `downgrade()` drops the constraint and the
column. It is lossy by nature (in-app role assignments are discarded), which is
inherent to rolling back the feature that introduced them — but it is safe for
the *deployment*, because the pre-#740 resolver reads the env allowlist alone and
so still resolves the same admins it did before.

Revision ID: f58b4bff54f7
Revises: bee3e56e1a5d
Create Date: 2026-08-16 00:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f58b4bff54f7"
down_revision: str | None = "bee3e56e1a5d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Kept as a literal rather than imported from `db.models`: a migration must
# describe the schema at THIS revision, and stay correct when a later revision
# widens the vocabulary.
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
    # `op.f(...)` with the FULL conventional name, matching the repo's other
    # constraint migrations. Passing the bare `"role_valid"` would leave the
    # final name up to whether this `MigrationContext` was configured with
    # `target_metadata` (env.py does; a bare `Operations` context does not) —
    # producing `ck_users_role_valid` in production and `role_valid` elsewhere,
    # off the same code. `downgrade()` would then fail to find its own
    # constraint. `op.f` marks the name as already-conventionalized, so it is
    # taken verbatim in both.
    op.create_check_constraint(
        op.f("ck_users_role_valid"),
        "users",
        sa.text("role IN ({})".format(", ".join(f"'{r}'" for r in _ROLES))),
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_users_role_valid"), "users", type_="check")
    op.drop_column("users", "role")
