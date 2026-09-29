"""coverage-loop schema (ADR 0047): suite/check origin, asset exclusion, incident resolution

Adds, all additive:

- ``suites.origin`` (``'user'`` default, CHECK ``user|auto``) and ``suites.auto_state`` (JSONB,
  NULL), plus the partial unique index ``uq_suites_auto_per_asset`` — one automatic suite per
  asset;
- ``checks.origin`` (``'user'`` default, CHECK ``user|auto|suggestion``);
- ``assets.auto_coverage_excluded`` (``false`` default);
- ``incidents.resolution`` (NULL, CHECK ``fixed|expected_change|false_positive``).

Every new NOT NULL column has a constant default, so PostgreSQL adds it without a table
rewrite; the CHECK constraints are added NOT VALID and validated after the transaction, and the
index is built CONCURRENTLY, so no step holds an exclusive lock for a table scan. The running
image never writes these columns, so this deploys ahead of the code.

Rollback: downgrade drops the index, constraints and columns. Safe while no automatic suite
exists; afterwards it discards which suites and checks the coverage loop owns.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0985227a6ccf"
down_revision: str | None = "18f2ee7908cf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CHECKS = (
    ("suites", "ck_suites_suite_origin_valid", "origin IN ('user', 'auto')"),
    ("checks", "ck_checks_check_origin_valid", "origin IN ('user', 'auto', 'suggestion')"),
    (
        "incidents",
        "ck_incidents_incident_resolution_valid",
        "resolution IN ('fixed', 'expected_change', 'false_positive')",
    ),
)


def upgrade() -> None:
    # Re-runnable throughout: the autocommit block below commits the columns and the unvalidated
    # constraints before the revision is recorded, so a timeout in VALIDATE or the index build
    # must not leave a migration that fails on "already exists" when retried.
    op.execute(
        "ALTER TABLE suites ADD COLUMN IF NOT EXISTS origin VARCHAR(16) NOT NULL DEFAULT 'user'"
    )
    op.execute("ALTER TABLE suites ADD COLUMN IF NOT EXISTS auto_state JSONB")
    op.execute(
        "ALTER TABLE checks ADD COLUMN IF NOT EXISTS origin VARCHAR(16) NOT NULL DEFAULT 'user'"
    )
    op.execute(
        "ALTER TABLE assets ADD COLUMN IF NOT EXISTS "
        "auto_coverage_excluded BOOLEAN NOT NULL DEFAULT false"
    )
    op.execute("ALTER TABLE incidents ADD COLUMN IF NOT EXISTS resolution VARCHAR(32)")
    # NOT VALID: adding the constraint is metadata-only; the row scan happens in VALIDATE below,
    # outside this transaction, under a lock that does not block reads or writes.
    for table, name, predicate in _CHECKS:
        # Every interpolated value is a constant above.
        add = f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({predicate}) NOT VALID"
        exists = f"SELECT 1 FROM pg_constraint WHERE conname = '{name}'"  # noqa: S608  # nosec B608
        op.execute(f"DO $$ BEGIN IF NOT EXISTS ({exists}) THEN {add}; END IF; END $$")
    with op.get_context().autocommit_block():
        for table, name, _ in _CHECKS:
            op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}")
        # A failed CONCURRENTLY build leaves an INVALID index that enforces nothing, and
        # IF NOT EXISTS would then skip the rebuild — drop it first.
        op.execute(
            "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_index WHERE indexrelid = "
            "to_regclass('uq_suites_auto_per_asset') AND NOT indisvalid) "
            "THEN EXECUTE 'DROP INDEX uq_suites_auto_per_asset'; END IF; END $$"
        )
        # One automatic suite per asset; CONCURRENTLY so building it never blocks `suites`.
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_suites_auto_per_asset "
            "ON suites (asset_id) WHERE origin = 'auto'"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS uq_suites_auto_per_asset")
    for table, name, _ in reversed(_CHECKS):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
    op.drop_column("incidents", "resolution")
    op.drop_column("assets", "auto_coverage_excluded")
    op.drop_column("checks", "origin")
    op.drop_column("suites", "auto_state")
    op.drop_column("suites", "origin")
