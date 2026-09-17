"""partial expression index on the RCA-narrative incident lookup (#1743)

`llm_rca.latest_narrative_invocation` filters `kind = 'rca_narrative' AND
status = 'succeeded' AND request ->> 'incident_id' = :id` — once per active
incident from the alert-dispatch path (`alerting/builder._incident_cards`),
against a table the G4 posture retains forever. Unindexed, that is one
sequential scan per incident per alerting run.

`ix_llm_invocations_rca_incident ON llm_invocations ((request ->> 'incident_id'),
created_at DESC, id DESC) WHERE kind = 'rca_narrative' AND status = 'succeeded'`
serves the equality AND the tiebreak ordering, and the partial predicate keeps
it to the succeeded-narrative slice (6% of a mixed 60k-row table in the
measurement on the PR).

Additive only. CONCURRENTLY like the sibling expression-index migrations
(`6bcf67868753`), since the table only grows.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "3d7c1a9fb204"
down_revision: str | None = "2017e2c7ee11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CREATE_SQL = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_llm_invocations_rca_incident "
    "ON llm_invocations ((request ->> 'incident_id'), created_at DESC, id DESC) "
    "WHERE kind = 'rca_narrative' AND status = 'succeeded'"
)
_DROP_SQL = "DROP INDEX CONCURRENTLY IF EXISTS ix_llm_invocations_rca_incident"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        # DROP first: a crashed earlier attempt can leave an INVALID index that
        # CREATE ... IF NOT EXISTS would then skip over.
        op.execute(_DROP_SQL)
        op.execute(_CREATE_SQL)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(_DROP_SQL)
