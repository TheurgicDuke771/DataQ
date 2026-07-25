"""Add `connections.credential_expires_at` — warn before a credential expires (#838).

Revision ID: e7f8a9b0c1d2
Revises: a7b8c9d0e1f2
Create Date: 2026-07-25

Prod lineage was dark for six days on an expired ADLS SAS. #828 made that state
visible once it broke something; this column is the other half — where a
credential states its own expiry (a SAS prints `se=`), DataQ can say so first.

**Backward compatible, no two-step needed.** The column is nullable with no
server default, so the currently-deployed code — which neither reads nor writes
it — keeps working unchanged against this schema. Pure widening: no data
rewrite, no backfill, no narrowing.

**Deliberately NOT backfilled**, and it cannot be: the value is derived from the
credential itself, which lives in the SecretStore, not in this database. A
migration has no business reading Key Vault. Existing rows populate on their next
secret write or on the next daily `refresh_credential_expiry` sweep — and until
then read as NULL, which the product renders as *unknown*, never as reassurance.

The stored value is a date, never credential material — safe to log and render.

Tested up and down locally. Down drops the column; the value is a recomputable
cache of the credential, so nothing is lost that the next sweep won't restore.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e7f8a9b0c1d2"
down_revision: str | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "connections",
        sa.Column("credential_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("connections", "credential_expires_at")
