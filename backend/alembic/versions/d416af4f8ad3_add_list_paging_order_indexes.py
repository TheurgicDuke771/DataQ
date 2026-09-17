"""newest-first paging indexes for /runs, /pipeline_runs and /incidents (#1245)

One ordering index per list table, matching each endpoint's total-order
`ORDER BY` exactly:

- `runs`        -> (created_at DESC, id DESC)
- `pipeline_runs` -> (created_at DESC, id DESC)
- `incidents`   -> (last_seen_at DESC, id DESC)

`incidents` pages by `last_seen_at`, NOT `created_at` (see
`incident_service.list_incidents`): ordering, filtering and windowing are all
on the most-recent-breach field, so a `created_at` index would never be used.

No filter-leading variant (`(status, created_at DESC, id DESC)` and friends)
is added: measured at 150k `runs` / 120k `pipeline_runs` / 120k `incidents`,
the ordering index alone already turns every filtered page-1 read into an
ordered index scan, and the composites only pay off at deep offsets on a
filtered list, which no product surface issues.

Additive, index-only. CONCURRENTLY, like the sibling index migrations — all
three tables are under continuous write load.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d416af4f8ad3"
down_revision: str | None = "b4e91c7a0d38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("ix_runs_created_id", "runs", "created_at DESC, id DESC"),
    ("ix_pipeline_runs_created_id", "pipeline_runs", "created_at DESC, id DESC"),
    ("ix_incidents_last_seen_id", "incidents", "last_seen_at DESC, id DESC"),
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for name, table, columns in _INDEXES:
            # DROP first: a crashed earlier attempt can leave an INVALID index that
            # CREATE ... IF NOT EXISTS would then skip over.
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
            op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {table} ({columns})")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name, _table, _columns in _INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
