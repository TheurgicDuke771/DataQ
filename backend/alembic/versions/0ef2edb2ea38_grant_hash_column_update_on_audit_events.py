"""Column-scoped UPDATE grant so the audit hash chain can seal rows (#1621).

The chain (#1460) sets `prev_hash`/`row_hash` after flush — an UPDATE — and the
append-only guard (ecda713656ac) revokes UPDATE from the app role, so every
audited mutation 500ed on the chain's first deploy. The grant covers ONLY the
two hash columns; the payload stays append-only.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0ef2edb2ea38"
down_revision: str | None = "5656bbfc1495"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "dataq_app"


def grant_statement(role: str) -> str:
    """A function, like ecda713656ac.revoke_statement, so the privilege test
    executes THIS SQL rather than a copy of it."""
    return (
        "DO $$ BEGIN "  # noqa: S608
        f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
        f"GRANT UPDATE (prev_hash, row_hash) ON audit_events TO {role}; "
        "END IF; END $$;"
    )  # nosec B608


def upgrade() -> None:
    op.execute(grant_statement(_APP_ROLE))


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN "  # noqa: S608
        f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN "
        f"REVOKE UPDATE (prev_hash, row_hash) ON audit_events FROM {_APP_ROLE}; "
        "END IF; END $$;"
    )  # nosec B608
