"""downgrade legacy admin shares to edit (ADR 0027 / #482)

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-30 02:00:00.000000+00:00

ADR 0027 removes grantable suite-``admin``: a normal user can only hold a
``view``/``edit`` share, and ``admin`` is now the *workspace-admin* (implicit on
every suite, never a ``shares`` row). The step-1 code stopped issuing new
``admin`` shares (the ``SharePermission`` Literal is ``view|edit`` and
``share_service`` rejects anything else) but deliberately kept reading any
pre-existing ``shares.permission = 'admin'`` rows as ``admin`` so nobody lost
access between the two deploys (backward-compatible).

This migration retires those legacy rows by downgrading them to ``edit`` — the
closest non-removed capability (they keep check-authoring/run access, lose
manage-shares/delete, which is now workspace-admin-only). After this runs there
should be no ``admin`` share rows; the new grant path already can't create more.

**Not a schema change.** The ``permission_valid`` CHECK still lists ``admin``
(``models.PERMISSIONS`` is unchanged) so the value stays legal at the DB layer —
this keeps a rollback to step-1 code safe and is harmless once the rows are gone.

**Tested up + down locally.** Down is a deliberate no-op: the original set of
``admin`` grantees is not recoverable (we can't tell which ``edit`` rows were
once ``admin``), and the affected users retain a valid ``edit`` share either way,
so there is no access regression to reverse. A rollback that genuinely needs the
old admins back must re-grant them out of band (and, pre-ADR-0027, via code that
still allowed it).
"""

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
    # Irreversible data migration: which 'edit' rows were once 'admin' is not
    # recorded, and the downgraded users keep a valid 'edit' share, so there is
    # no access state to restore. Intentional no-op.
    pass
