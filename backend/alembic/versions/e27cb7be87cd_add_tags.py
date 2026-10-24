"""tags + the four per-entity join tables (#888)

``tags`` is the workspace-shared vocabulary (unique ``name``); ``suite_tags`` / ``asset_tags`` /
``connection_tags`` / ``check_tags`` hold the assignments, each with real foreign keys and
``ON DELETE CASCADE`` in both directions — deleting an entity removes its assignments, deleting a
tag detaches it everywhere. Deliberately not one polymorphic ``(entity_type, entity_id)`` table,
which would have no FK and make orphan cleanup the application's problem.

Purely additive: no existing table changes, so the running release is unaffected and the
migration can deploy ahead of the code that reads the tables.

Rollback: ``downgrade`` drops the five tables (assignments included). Tag data is lost; nothing
else references these tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e27cb7be87cd"
down_revision: str | None = "c38847820d74"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (join table, parent table, parent id column) — dropped in reverse order on downgrade.
_JOIN_TABLES: tuple[tuple[str, str, str], ...] = (
    ("suite_tags", "suites", "suite_id"),
    ("asset_tags", "assets", "asset_id"),
    ("connection_tags", "connections", "connection_id"),
    ("check_tags", "checks", "check_id"),
)


def upgrade() -> None:
    op.create_table(
        "tags",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tags")),
        sa.UniqueConstraint("name", name=op.f("uq_tags_name")),
    )
    for table, parent, column in _JOIN_TABLES:
        op.create_table(
            table,
            sa.Column(column, postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("tag_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.ForeignKeyConstraint(
                [column],
                [f"{parent}.id"],
                name=op.f(f"fk_{table}_{column}_{parent}"),
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["tag_id"],
                ["tags.id"],
                name=op.f(f"fk_{table}_tag_id_tags"),
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint(column, "tag_id", name=op.f(f"pk_{table}")),
        )
        # The PK serves `<entity> → tags`; this index serves the reverse (`tag → every entity`,
        # which the AND filter and the vocabulary's usage counts walk).
        op.create_index(op.f(f"ix_{table}_tag_id"), table, ["tag_id"], unique=False)


def downgrade() -> None:
    for table, _parent, _column in reversed(_JOIN_TABLES):
        op.drop_index(op.f(f"ix_{table}_tag_id"), table_name=table)
        op.drop_table(table)
    op.drop_table("tags")
