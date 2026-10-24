"""widen the trigger-provider value-sets for suite-on-suite triggers (ADR 0051, #244)

``suite`` joins `trigger_bindings.provider` and the `uq_runs_suite_triggered_by` dedup
predicate. Nothing else widens: a suite is not an orchestration provider, so `connections.type`,
`pipeline_runs.provider` and the orchestrator-connection index are untouched.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7a1c5d9e2b44"
down_revision: str | None = "e27cb7be87cd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WITH_SUITE = "'adf', 'airflow', 'dbt', 'suite'"
_NO_SUITE = "'adf', 'airflow', 'dbt'"
_DEDUP_WITH_SUITE = (
    "triggered_by LIKE 'adf:%' OR triggered_by LIKE 'airflow:%' OR triggered_by LIKE 'dbt:%' "
    "OR triggered_by LIKE 'suite:%'"
)
_DEDUP_NO_SUITE = (
    "triggered_by LIKE 'adf:%' OR triggered_by LIKE 'airflow:%' OR triggered_by LIKE 'dbt:%'"
)


def _set_provider_check(values: str) -> None:
    op.execute(
        "ALTER TABLE trigger_bindings DROP CONSTRAINT IF EXISTS ck_trigger_bindings_provider_valid"
    )
    op.execute(
        "ALTER TABLE trigger_bindings ADD CONSTRAINT ck_trigger_bindings_provider_valid "
        f"CHECK (provider IN ({values}))"
    )


def _index_statements(predicate: str) -> tuple[str, str, str]:
    """(create-next CONCURRENTLY, drop-old CONCURRENTLY, rename) — exposed so the test can run
    them with CONCURRENTLY stripped inside its transaction."""
    return (
        "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_runs_suite_triggered_by_next "
        f"ON runs (suite_id, triggered_by) WHERE {predicate}",
        "DROP INDEX CONCURRENTLY IF EXISTS uq_runs_suite_triggered_by",
        "ALTER INDEX uq_runs_suite_triggered_by_next RENAME TO uq_runs_suite_triggered_by",
    )


def _set_trigger_dedup_index(predicate: str) -> None:
    """Rebuild the partial unique index without holding `runs` locked for the build: create the
    widened one CONCURRENTLY under a scratch name (outside alembic's transaction — Postgres
    refuses CONCURRENTLY inside one), drop the old one CONCURRENTLY, then rename. Each step is
    idempotent for a partial retry.
    """
    create, drop, rename = _index_statements(predicate)
    with op.get_context().autocommit_block():
        op.execute(create)
        op.execute(drop)
    op.execute(rename)


def upgrade() -> None:
    _set_provider_check(_WITH_SUITE)
    _set_trigger_dedup_index(_DEDUP_WITH_SUITE)


def downgrade() -> None:
    # Safe only while no `suite` binding or `suite:`-marked run exists; the CHECK would otherwise
    # refuse to re-attach.
    _set_trigger_dedup_index(_DEDUP_NO_SUITE)
    _set_provider_check(_NO_SUITE)
