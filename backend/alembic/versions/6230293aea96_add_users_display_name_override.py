"""users: add display_name_override — tells a self-set name from a claim-seeded one (#1139)

`_upsert_user`/`_claim_unlinked_user` (backend/app/core/auth.py) used to COALESCE
`display_name` onto whatever was already stored, so a `PATCH /me` override would
survive a later AAD login. Review on #1139 caught the regression that shipped
with it: COALESCE can't tell "someone set this on purpose" from "a first login
seeded it from the token claim", so it also froze the name at whatever the FIRST
AAD login happened to claim — a legitimate Entra rename never synced again for
ANY user, override or not.

This column is the missing bit: `TRUE` once a human has explicitly set their own
name via `PATCH /me`, `FALSE` otherwise (including every pre-existing row, and
every row a bare AAD/OTP login seeds a name for). The upsert/claim paths now
branch on it instead of on nullability — sync the claim into `display_name`
whenever the flag is `FALSE` (restores rename-sync for everyone who never
overrode it), and leave `display_name` alone whenever it's `TRUE`.

Purely additive and backward-compatible (CLAUDE.md migration rules): one
**NOT NULL DEFAULT false** column on `users` — existing rows get `false`
(nothing was ever self-service-set before this shipped, so that is the
correct historical value, not a placeholder), no backfill, no other table
touched. Code deployed before this migration keeps working unmodified (it
never reads or writes the column); code deployed after works against either
migration state without change, since `false` is exactly what "no override
yet" already meant implicitly.

Revision ID: 6230293aea96
Revises: d0b7ecd7b675
Create Date: 2026-08-02 00:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6230293aea96"
down_revision: str | None = "d0b7ecd7b675"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "display_name_override",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "display_name_override")
