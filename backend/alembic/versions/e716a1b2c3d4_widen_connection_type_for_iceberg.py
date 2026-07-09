"""widen connection type-set for the native iceberg datasource

Adds ``iceberg`` as a datasource connection type (ADR 0030, #716) — the native
`pyiceberg` read path. Iceberg is a *datasource* (CLAUDE.md §4), not an
orchestration provider, so — unlike the dbt widening (``c1d2e3f4a5b6``) — only the
**one** datasource-facing constraint changes; the orchestration value-sets
(provider CHECKs, orchestrator index, trigger-dedup predicate) are untouched.

Single widening, **additive** (permit one more ``type`` value) and therefore
backward-compatible: old code that never emits ``iceberg`` is unaffected, and no
existing row can violate a widened CHECK.

* ``ck_connections_type_valid`` — allow an ``iceberg`` connection row.

Tested up + down locally. Raw SQL (exact constraint name) mirrors
``c1d2e3f4a5b6``; kept in sync with ``CONNECTION_TYPES`` in ``db/models.py``.

**Lock footprint:** re-adding the CHECK full-scan-validates ``connections`` under
a brief ACCESS EXCLUSIVE lock, in one transaction — sub-second at demo/harness
sizes. If ``connections`` ever grows large, split into ``NOT VALID`` +
``VALIDATE CONSTRAINT`` (per the note on ``c1d2e3f4a5b6``).

**Downgrade window:** this PR wires ``IcebergConnectionAdapter`` into the registry
(no feature flag), so an ``iceberg`` connection can land minutes after deploy —
``downgrade`` is only safe in the brief gap *before any iceberg row exists*. After
that the re-added CHECK rejects the narrowing (whole txn aborts atomically); the
recovery is to roll forward, not back.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e716a1b2c3d4"
down_revision: str | None = "c605d1e2f3a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONNECTION_TYPES_WITH_ICEBERG = (
    "'snowflake', 'adls_gen2', 's3', 'unity_catalog', 'iceberg', 'adf', 'airflow', 'dbt'"
)
_CONNECTION_TYPES_NO_ICEBERG = (
    "'snowflake', 'adls_gen2', 's3', 'unity_catalog', 'adf', 'airflow', 'dbt'"
)


def _set_type_check(values: str) -> None:
    # IF EXISTS on the drop so a partial-retry after an aborted run re-applies cleanly.
    op.execute("ALTER TABLE connections DROP CONSTRAINT IF EXISTS ck_connections_type_valid")
    op.execute(
        "ALTER TABLE connections ADD CONSTRAINT ck_connections_type_valid "
        f"CHECK (type IN ({values}))"
    )


def upgrade() -> None:
    _set_type_check(_CONNECTION_TYPES_WITH_ICEBERG)


def downgrade() -> None:
    # Safe only before any iceberg connection row exists (this PR ships the adapter
    # unflagged, so that window closes at first iceberg connection). A rollback
    # afterwards fails the re-added CHECK; recovery is roll-forward.
    _set_type_check(_CONNECTION_TYPES_NO_ICEBERG)
