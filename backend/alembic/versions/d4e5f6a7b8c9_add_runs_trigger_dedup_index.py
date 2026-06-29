"""add partial unique index on runs(suite_id, triggered_by) for orchestration markers

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-29 12:00:00.000000+00:00

Closes the trigger-dedup race (#308). ``orchestration_service._trigger_suites``
created a suite run per enabled binding with a non-atomic SELECT-then-INSERT and
no DB constraint, so two concurrent ingestions of the *same* pipeline-run event
(webhook + 10-min poll, or poll + startup gap-recovery) could both pass the
"does a run already exist?" check and double-trigger a suite.

Fix = a **partial** unique index on ``(suite_id, triggered_by)`` so a duplicate
insert fails at the DB; the service pairs it with ``ON CONFLICT DO NOTHING`` so
the loser of the race is a graceful no-op instead of an IntegrityError.

The index is **partial — orchestration markers only** (``adf:`` / ``airflow:``,
the ``<provider>:<pipeline>:<run_id>`` shape, CLAUDE.md §10). The other
``triggered_by`` namespaces — ``manual:<uid>``, ``probe:<uid>``,
``schedule:<id>`` — legitimately repeat for the same suite (you can run a suite
manually twice, a schedule fires it on every tick) and must NOT be deduped. A
*positive* predicate (name the providers) degrades safely: a future provider not
yet listed simply falls back to today's pre-index behaviour (the in-app SELECT
check), never a regression — whereas an exclude-list predicate would silently
drop a legitimate repeat run if a new internal trigger namespace were added.

Backward-compatible: additive index, no column/schema change, deployable ahead
of the service change. Any pre-existing orchestration-marker duplicates (only
reachable via the very race being fixed) are collapsed to the earliest run per
group first, so the unique index can be created on the live DB without failing;
the duplicate runs were erroneous (one pipeline-run must map to one suite run)
and their results cascade-delete with them.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Literal SQL (no interpolation) — the orchestration-marker predicate below is
# kept in sync with ``orchestration_service._ORCH_TRIGGER_PREDICATE`` and the
# model's ``postgresql_where`` on the `uq_runs_suite_triggered_by` index.


def upgrade() -> None:
    # Collapse any erroneous orchestration-marker duplicates (keep the earliest
    # run per (suite_id, triggered_by)) so the unique index builds cleanly on a
    # live DB. Results cascade-delete via results.run_id ON DELETE CASCADE.
    op.execute("""
        DELETE FROM runs r
        USING (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY suite_id, triggered_by
                       ORDER BY created_at, id
                   ) AS rn
            FROM runs
            WHERE triggered_by LIKE 'adf:%' OR triggered_by LIKE 'airflow:%'
        ) dup
        WHERE r.id = dup.id AND dup.rn > 1
        """)
    op.execute("""
        CREATE UNIQUE INDEX uq_runs_suite_triggered_by
        ON runs (suite_id, triggered_by)
        WHERE triggered_by LIKE 'adf:%' OR triggered_by LIKE 'airflow:%'
        """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_runs_suite_triggered_by")
