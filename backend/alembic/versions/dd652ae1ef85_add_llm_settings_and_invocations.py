"""llm_settings (singleton provider config) + llm_invocations (round-trip /
audit / cost record) — the ADR 0042 outbound-LLM seam (#1511). Additive only.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "dd652ae1ef85"
down_revision: str | None = "0ef2edb2ea38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("base_url", sa.String(512), nullable=True),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("api_key_secret_ref", sa.String(256), nullable=True),
        sa.Column(
            "structured_output", sa.String(16), nullable=False, server_default=sa.text("'native'")
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "provider IN ('anthropic', 'openai_compatible')",
            name="ck_llm_settings_llm_provider_valid",
        ),
        sa.CheckConstraint(
            "structured_output IN ('native', 'prompt_json')",
            name="ck_llm_settings_llm_structured_output_valid",
        ),
    )
    op.create_table(
        "llm_invocations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'pending'")),
        sa.Column(
            "requested_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "suite_id",
            UUID(as_uuid=True),
            sa.ForeignKey("suites.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("request", JSONB(none_as_null=True), nullable=True),
        sa.Column("context_fingerprint", sa.String(64), nullable=True),
        sa.Column("response", JSONB(none_as_null=True), nullable=True),
        sa.Column("error", sa.String(1024), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('ping', 'sql_generation', 'check_suggestion', 'rca_narrative')",
            name="ck_llm_invocations_llm_invocation_kind_valid",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name="ck_llm_invocations_llm_invocation_status_valid",
        ),
    )
    op.create_index("ix_llm_invocations_requested_by", "llm_invocations", ["requested_by_user_id"])


def downgrade() -> None:
    op.drop_index("ix_llm_invocations_requested_by", table_name="llm_invocations")
    op.drop_table("llm_invocations")
    op.drop_table("llm_settings")
