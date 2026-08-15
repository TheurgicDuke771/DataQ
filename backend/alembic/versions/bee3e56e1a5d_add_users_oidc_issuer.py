"""add users.oidc_issuer (provider-neutral auth, ADR 0026 amendment)

Backend OIDC validation was Azure-AD-only (`fastapi_azure_auth`, Entra-specific
claim names) despite the frontend contract (ADR 0028) already being
provider-neutral. This is the schema half of closing that gap: a second OIDC
issuer (e.g. AWS Cognito) can now authenticate, disambiguated from Azure AD by
this column paired with the existing `aad_object_id`.

Additive & backward-compatible (CLAUDE.md migration rules): a single
**nullable** column, no default, no backfill. Existing rows self-heal on their
next login (both the Azure and the new generic-OIDC path write it on every
upsert) rather than needing a backfill step here. Nothing reads it until the
paired code PR ships, so this migration is safe to deploy on its own
(two-step discipline).

`aad_object_id` itself is untouched — not renamed, not re-typed. See the
`User` model docstring (`backend/app/db/models.py`) for why.

Revision ID: bee3e56e1a5d
Revises: 4d23b47ee564
Create Date: 2026-08-15 00:00:00.000000+00:00

"""

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
