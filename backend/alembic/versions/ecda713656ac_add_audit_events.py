"""audit_events: the append-only cross-entity audit log — ADR 0041 phase 1 (#1318)"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ecda713656ac"
down_revision: str | None = "f58b4bff54f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Literals, not imports from `db.models`: a migration describes the schema at THIS
# revision and must stay correct when a later revision widens the vocabulary.
_ACTION_CLASSES = ("config", "access")
_ACTOR_KINDS = ("user", "pat", "webhook")

#: The application role the deployed stack runs as. Kept as a literal for the
#: same reason as the vocabularies above.
_APP_ROLE = "dataq_app"


def _in_list(column: str, values: tuple[str, ...]) -> str:
    return "{} IN ({})".format(column, ", ".join(f"'{v}'" for v in values))


def revoke_statement(role: str) -> str:
    """The append-only guard, as a statement — deliberately a function so the
    migration test can execute *this* SQL rather than a copy of it.
    """
    return (
        "DO $$ BEGIN "  # noqa: S608
        f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
        f"REVOKE UPDATE, DELETE ON audit_events FROM {role}; "
        "END IF; END $$;"
    )  # nosec B608


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("action_class", sa.String(length=16), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        # No ForeignKey — see the module docstring.
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_kind", sa.String(length=16), nullable=False),
        sa.Column("actor_label", sa.String(length=320), nullable=True),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_audit_events_actor_user_id_users"),
            ondelete="SET NULL",
        ),
        # `op.f(...)` with the full conventional name — see f58b4bff54f7's note: a bare name
        # resolves differently depending on whether the migration context carries `target_metadata`.
        sa.CheckConstraint(
            _in_list("action_class", _ACTION_CLASSES),
            name=op.f("ck_audit_events_action_class_valid"),
        ),
        sa.CheckConstraint(
            _in_list("actor_kind", _ACTOR_KINDS),
            name=op.f("ck_audit_events_actor_kind_valid"),
        ),
    )
    # "Everything that happened to this entity", newest first.
    op.create_index(
        "ix_audit_events_entity",
        "audit_events",
        ["entity_type", "entity_id", sa.text("occurred_at DESC")],
    )
    # The class-scoped feed.
    op.create_index(
        "ix_audit_events_class_occurred",
        "audit_events",
        ["action_class", sa.text("occurred_at DESC")],
    )
    # "What did this principal do", newest first.
    op.create_index(
        "ix_audit_events_actor",
        "audit_events",
        ["actor_user_id", sa.text("occurred_at DESC")],
    )
    # Append-only guard.
    op.execute(revoke_statement(_APP_ROLE))


def downgrade() -> None:
    op.drop_index("ix_audit_events_actor", table_name="audit_events")
    op.drop_index("ix_audit_events_class_occurred", table_name="audit_events")
    op.drop_index("ix_audit_events_entity", table_name="audit_events")
    op.drop_table("audit_events")
