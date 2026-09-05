"""add workspace_health.payload — structured signal detail (#1886)

The table has carried only `key`/`alerted_at`/`updated_at` since #1052: every signal
so far either needed no detail (the near-miss markers decode their own key) or had a
dedicated read path elsewhere (beat heartbeat, poll staleness — both #1885, read off
`Connection`). The orphan-secret sweep report (#1886) is the first signal that IS the
detail — ran_at/mode/orphan_count/orphan_names/store/error — so it needs a payload
column rather than another single-purpose table. Additive, nullable: existing rows
(the poll-staleness flag, near-miss markers, the beat heartbeat) are untouched.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "21d739b1d293"
down_revision: str | None = "6bcf67868753"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("workspace_health", sa.Column("payload", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("workspace_health", "payload")
