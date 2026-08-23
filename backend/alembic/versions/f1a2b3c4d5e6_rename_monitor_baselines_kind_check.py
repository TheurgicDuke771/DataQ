"""Rename the double-prefixed `monitor_baselines` kind CHECK to its intended name (#990)."""

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
    """Rename `old`→`new` only if `old` exists and `new` does not."""
    bind = op.get_bind()
    present = bind.scalar(text("SELECT 1 FROM pg_constraint WHERE conname = :name"), {"name": old})
    already = bind.scalar(text("SELECT 1 FROM pg_constraint WHERE conname = :name"), {"name": new})
    if not present or already:
        return
    # Interpolated, because an identifier cannot be a bind parameter in ALTER TABLE.
    op.execute(f'ALTER TABLE monitor_baselines RENAME CONSTRAINT "{old}" TO "{new}"')


def upgrade() -> None:
    _rename(_DOUBLED, _INTENDED)


def downgrade() -> None:
    _rename(_INTENDED, _DOUBLED)
