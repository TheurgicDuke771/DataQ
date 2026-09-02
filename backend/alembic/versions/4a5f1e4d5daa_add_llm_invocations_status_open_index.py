"""partial index on llm_invocations(status) for the open states (#1717)

Additive only: `ix_llm_invocations_status_open ON llm_invocations (status)
WHERE status IN ('pending', 'running')`. The #1644 reaper filters
`status = 'pending'` / `status = 'running'` every beat tick; both equalities
imply the IN-list, so the planner can use the index, and the index holds only
the handful of in-flight rows while the retained-forever table grows.

Not CONCURRENTLY: the table is small (the LLM track shipped in W3) and a plain
CREATE INDEX keeps the migration transactional, so a failed upgrade leaves
nothing half-built.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "4a5f1e4d5daa"
down_revision: str | None = "cadc40254699"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_llm_invocations_status_open",
        "llm_invocations",
        ["status"],
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("ix_llm_invocations_status_open", table_name="llm_invocations")
