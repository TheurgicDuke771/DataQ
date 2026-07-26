"""Add credential_expiry_checked_at so NULL expiry means one thing (#1024).

Revision ID: a9b8c7d6e5f4
Revises: e1f2a3b4c5d6
Create Date: 2026-07-26

`credential_expires_at` NULL currently carries two meanings that a reader cannot
tell apart:

* the credential states no expiry — a Snowflake PAT, an S3 access key. Correct,
  permanent silence.
* nobody has looked yet — a connection whose secret predates #838, or an instance
  deployed less than a sweep ago.

Both render as "nothing expires soon", so the warning surface reads as
*reassuring* precisely when it has no idea. Prod showed exactly this after the
2026-07-26 deploy: every connection NULL, including SAS-bearing ones whose expiry
is printed in the token.

This column is stamped on **every** refresh attempt regardless of outcome, so:

    checked_at NULL, expires_at NULL  -> never looked; say nothing
    checked_at set,  expires_at NULL  -> looked; no readable expiry; correct silence
    checked_at set,  expires_at set   -> warn inside the window

**Backward compatible, no two-step.** Nullable with no server default, so the
currently-deployed code — which never reads or writes it — keeps working against
this schema. Pure widening: no data rewrite, no backfill, no narrowing.

**Existing rows are deliberately NOT backfilled.** Stamping "checked" for rows
nobody has read would assert the very thing this column exists to distinguish.
They stay NULL — honestly unknown — until the next sweep, which now runs at beat
start rather than only daily.

Tested up and down locally. Down drops the column, losing only the
looked-vs-unknown distinction for rows checked while it was applied — acceptable
for a nullable additive column with no dependent data.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a9b8c7d6e5f4"
down_revision: str | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "connections",
        sa.Column("credential_expiry_checked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("connections", "credential_expiry_checked_at")
