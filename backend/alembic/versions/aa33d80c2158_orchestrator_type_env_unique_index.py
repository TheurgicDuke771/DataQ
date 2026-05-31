"""orchestrator (type, env) partial unique index (#72)

Enforce one orchestration-provider connection per (provider, env): a partial
UNIQUE index on (type, env) WHERE type IN ('adf', 'airflow'). This backs the
`trigger_bindings` single-orchestrator-per-(provider, env) assumption (ADR 0004)
so a webhook/poll can resolve the one ADF/Airflow connection for an env without
ambiguity.

Partial by design — datasources are excluded: Snowflake DEV (and ADLS / S3 / UC)
may legitimately have many connections per env (different databases / buckets),
so only the two orchestration types are constrained.

Why backward-compatible: a new partial index only — no column changes, no data
rewrite. Existing rows are unaffected (no env currently holds two orchestrator
rows of the same type); old code keeps working, new inserts that would create a
second ADF/Airflow connection in an env now fail with a unique violation, which
the connection service maps to a 409.

Note for production: against a populated DB, create this with
postgresql_concurrently=True and disable Alembic's transactional DDL for the
migration. The non-concurrent form below is fine today because the DB is empty.

Revision ID: aa33d80c2158
Revises: cf42d364f74b
Create Date: 2026-05-31 00:19:45.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "aa33d80c2158"
down_revision: str | None = "cf42d364f74b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "uq_connections_orchestrator_type_env"


def upgrade() -> None:
    op.create_index(
        _INDEX_NAME,
        "connections",
        ["type", "env"],
        unique=True,
        postgresql_where=sa.text("type IN ('adf', 'airflow')"),
    )


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="connections")
