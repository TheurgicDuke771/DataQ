"""widen orchestration value-sets + trigger-dedup predicate for the dbt provider

Adds ``dbt`` as a third `OrchestrationProvider` (ADR 0029, #611). dbt is an
orchestration provider, not a datasource (CLAUDE.md §4), so it joins the same
value-sets ADF/Airflow live in. Five widenings, all **additive** (permit one more
value) and therefore backward-compatible — old code that never emits ``dbt`` is
unaffected, and no existing row can violate a widened constraint:

1. ``ck_connections_type_valid``      — allow a ``dbt`` connection row.
2. ``uq_connections_orchestrator_type_env`` — one dbt connection per env (as ADF/Airflow).
3. ``ck_pipeline_runs_provider_valid`` — allow ``provider='dbt'`` pipeline runs.
4. ``ck_trigger_bindings_provider_valid`` — allow ``provider='dbt'`` trigger bindings.
5. ``uq_runs_suite_triggered_by``      — extend the trigger-dedup predicate to ``dbt:%``.

Tested up + down locally. Raw SQL (exact constraint/index names) mirrors the #308
dedup-index migration; kept in sync with the model constraints in `db/models.py`
and `orchestration_service._ORCH_TRIGGER_PREDICATE`.

**Lock footprint (deliberate):** the two index rebuilds `DROP` + non-concurrently
`CREATE UNIQUE INDEX`, taking a brief ACCESS EXCLUSIVE lock on `connections` and
`runs`, and the CHECK re-adds full-scan-validate — all in one transaction. This is
the *gap-free* choice on purpose: staying transactional means a concurrent
orchestration webhook's `ON CONFLICT (uq_runs_suite_triggered_by)` blocks on the
lock and resumes against the rebuilt index, never hitting a "no matching unique
constraint" window that a `CREATE INDEX CONCURRENTLY` rebuild would expose. At the
current demo/harness table sizes the lock is sub-second, so this is the right
trade-off. If `connections`/`runs` ever grow large, split the index rebuilds into a
`postgresql_concurrently=True` migration (per the note on `aa33d80c2158`) and the
CHECKs into `NOT VALID` + `VALIDATE CONSTRAINT`.

**Downgrade window:** this PR also wires `DbtProvider` into the registry (no feature
flag), so a `dbt` connection/webhook can land minutes after deploy — ``downgrade``
is only safe in the brief gap *before any dbt activity*, not "until the service
ships". After that, roll forward, not back (see ``downgrade``).
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: str | None = "b0c1d2e3f4a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONNECTION_TYPES_WITH_DBT = (
    "'snowflake', 'adls_gen2', 's3', 'unity_catalog', 'adf', 'airflow', 'dbt'"
)
_CONNECTION_TYPES_NO_DBT = "'snowflake', 'adls_gen2', 's3', 'unity_catalog', 'adf', 'airflow'"


def _set_type_check(values: str) -> None:
    # IF EXISTS on the drop (like the index drops) so a partial-retry after an
    # aborted run re-applies cleanly.
    op.execute("ALTER TABLE connections DROP CONSTRAINT IF EXISTS ck_connections_type_valid")
    op.execute(
        "ALTER TABLE connections ADD CONSTRAINT ck_connections_type_valid "
        f"CHECK (type IN ({values}))"
    )


def _set_provider_check(table: str, values: str) -> None:
    name = f"ck_{table}_provider_valid"
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
    op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK (provider IN ({values}))")


def _set_orchestrator_index(types: str) -> None:
    op.execute("DROP INDEX IF EXISTS uq_connections_orchestrator_type_env")
    op.execute(
        "CREATE UNIQUE INDEX uq_connections_orchestrator_type_env "
        f"ON connections (type, env) WHERE type IN ({types})"
    )


def _set_trigger_dedup_index(predicate: str) -> None:
    op.execute("DROP INDEX IF EXISTS uq_runs_suite_triggered_by")
    op.execute(
        "CREATE UNIQUE INDEX uq_runs_suite_triggered_by "
        f"ON runs (suite_id, triggered_by) WHERE {predicate}"
    )


_ORCH_TYPES_WITH_DBT = "'adf', 'airflow', 'dbt'"
_ORCH_TYPES_NO_DBT = "'adf', 'airflow'"
_DEDUP_WITH_DBT = (
    "triggered_by LIKE 'adf:%' OR triggered_by LIKE 'airflow:%' OR triggered_by LIKE 'dbt:%'"
)
_DEDUP_NO_DBT = "triggered_by LIKE 'adf:%' OR triggered_by LIKE 'airflow:%'"


def upgrade() -> None:
    _set_type_check(_CONNECTION_TYPES_WITH_DBT)
    _set_orchestrator_index(_ORCH_TYPES_WITH_DBT)
    _set_provider_check("pipeline_runs", _ORCH_TYPES_WITH_DBT)
    _set_provider_check("trigger_bindings", _ORCH_TYPES_WITH_DBT)
    _set_trigger_dedup_index(_DEDUP_WITH_DBT)


def downgrade() -> None:
    # Narrowing back is only safe in the brief window before any dbt row exists —
    # this PR ships DbtProvider unflagged, so that window closes as soon as the
    # first dbt connection/webhook lands. A rollback after dbt data exists fails the
    # re-added CHECK / UNIQUE INDEX (whole txn aborts atomically, no partial state);
    # the intended recovery past that point is to roll forward, not back.
    _set_trigger_dedup_index(_DEDUP_NO_DBT)
    _set_provider_check("trigger_bindings", _ORCH_TYPES_NO_DBT)
    _set_provider_check("pipeline_runs", _ORCH_TYPES_NO_DBT)
    _set_orchestrator_index(_ORCH_TYPES_NO_DBT)
    _set_type_check(_CONNECTION_TYPES_NO_DBT)
