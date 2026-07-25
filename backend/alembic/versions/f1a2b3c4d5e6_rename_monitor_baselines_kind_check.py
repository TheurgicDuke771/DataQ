"""Rename the double-prefixed `monitor_baselines` kind CHECK to its intended name (#990).

Revision ID: f1a2b3c4d5e6
Revises: e7f8a9b0c1d2
Create Date: 2026-07-25

Found by the model-vs-migration parity check added with #990 — the first drift it
surfaced that Alembic's own `compare_metadata` is blind to (it never inspects a
CHECK constraint's presence or body).

`e2f3a4b5c6d7` created the constraint with ``name="ck_monitor_baselines_kind_valid"``
— already fully expanded. But `op.create_table` applies the target metadata's
naming convention (``ck_%(table_name)s_%(constraint_name)s``), which expanded it a
second time, so every database built by migrations carries
``ck_monitor_baselines_ck_monitor_baselines_kind_valid`` while every database built
by `create_all` carries ``ck_monitor_baselines_kind_valid``.

The constraint's *body* is identical, so nothing has ever been mis-enforced. What
differed is its **identity** — and this codebase does key on constraint names:
`connection_service._conflict_from_integrity_error` reads
``diag.constraint_name`` to choose between two different 409 responses. A test
and a production database disagreeing about a constraint's name is the same class
of split that makes a suite green while production behaves differently.

**Backward compatible.** A rename touches no data and no column; the currently
deployed code references neither name (verified by grep across `backend/app`), so
old and new code both run unchanged against either spelling. `IF EXISTS`-style
guards are used on both legs so the migration is a no-op on a database that
already has the intended name — notably any database built by `create_all`.

Tested up and down locally.
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "e7f8a9b0c1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DOUBLED = "ck_monitor_baselines_ck_monitor_baselines_kind_valid"
_INTENDED = "ck_monitor_baselines_kind_valid"


def _rename(old: str, new: str) -> None:
    """Rename `old`→`new` only if `old` exists and `new` does not.

    Postgres has no ``ALTER TABLE … RENAME CONSTRAINT IF EXISTS``, and this
    revision must apply cleanly to both database lineages: one built by the
    migration chain (which has the doubled name) and one built by `create_all`
    (which already has the intended one).
    """
    bind = op.get_bind()
    present = bind.scalar(text("SELECT 1 FROM pg_constraint WHERE conname = :name"), {"name": old})
    already = bind.scalar(text("SELECT 1 FROM pg_constraint WHERE conname = :name"), {"name": new})
    if not present or already:
        return
    # Interpolated, because an identifier cannot be a bind parameter in ALTER
    # TABLE. Safe: both names are module constants in this file, never caller
    # input. No suppression marker — neither Ruff nor CI's Bandit (which scans
    # `backend/app/` only) flags this form, and a marker for a warning that isn't
    # raised is noise that later reads as a real, live suppression.
    op.execute(f'ALTER TABLE monitor_baselines RENAME CONSTRAINT "{old}" TO "{new}"')


def upgrade() -> None:
    _rename(_DOUBLED, _INTENDED)


def downgrade() -> None:
    _rename(_INTENDED, _DOUBLED)
