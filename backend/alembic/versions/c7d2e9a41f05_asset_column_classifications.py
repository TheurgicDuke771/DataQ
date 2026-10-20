"""asset_column_classifications + assets.detected_classifications_refreshed_at — the profiler
classification source (ADR 0049, #1674)

Additive: one new table and one nullable column. Nothing is dropped and no existing column
changes meaning. The table ships EMPTY and the column NULL; until the code that writes them
rolls, every redaction read behaves exactly as before, because an empty overlay adds nothing.

Rollback: `alembic downgrade -1` drops the index, the table and the column. Tested up, down and
up again against a scratch Postgres database holding rows.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c7d2e9a41f05"
down_revision: str | None = "b94674780cb8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copies of db.models' vocabularies at migration time — a migration must not import the
# live model (the model moves on; this file must keep producing the same schema).
_SOURCES = ("profiler",)
_VERDICTS = ("sensitive",)
_DETECTORS = ("email", "encoded")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.create_table(
        "asset_column_classifications",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("column_name", sa.Text(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("verdict", sa.String(16), nullable=False),
        sa.Column("detector", sa.String(32), nullable=False),
        # Counts only — never a value (the standing "no warehouse data in a stored payload" rule).
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("first_detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "dismissed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        # Short names: the env's naming convention prefixes `ck_<table>_`, as for the model.
        sa.CheckConstraint(_in("source", _SOURCES), name="source_valid"),
        sa.CheckConstraint(_in("verdict", _VERDICTS), name="verdict_valid"),
        sa.CheckConstraint(_in("detector", _DETECTORS), name="detector_valid"),
        sa.CheckConstraint("jsonb_typeof(evidence) = 'object'", name="evidence_is_object"),
        sa.CheckConstraint("last_detected_at >= first_detected_at", name="detected_order"),
    )
    # One row per (asset, source, column), case-folded like the redaction map's keys. An
    # expression index: Postgres cannot express a unique CONSTRAINT over an expression.
    op.create_index(
        "uq_asset_column_classifications_cell",
        "asset_column_classifications",
        ["asset_id", "source", sa.text("lower(column_name)")],
        unique=True,
    )
    op.add_column(
        "assets",
        sa.Column(
            "detected_classifications_refreshed_at", sa.DateTime(timezone=True), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("assets", "detected_classifications_refreshed_at")
    op.drop_index("uq_asset_column_classifications_cell", table_name="asset_column_classifications")
    op.drop_table("asset_column_classifications")
