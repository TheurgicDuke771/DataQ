"""sessions + otp_codes: the email-OTP verifier stores (ADR 0032 decisions 3+4, #734)"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d0b7ecd7b675"
down_revision: str | None = "7d25617cfaf0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Index names must match `UserSession.__table_args__` / `OtpCode.__table_args__` in
# `backend/app/db/models.py` exactly.
_SESSIONS_TOKEN_HASH_UQ = "uq_sessions_token_hash"  # noqa: S105 — an index name
_SESSIONS_USER_ID_IX = "ix_sessions_user_id"
_OTP_EMAIL_CREATED_IX = "ix_otp_codes_email_created_at"


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(_SESSIONS_USER_ID_IX, "sessions", ["user_id"])
    # Doubles as the O(1) auth lookup index on every authenticated request.
    op.create_index(_SESSIONS_TOKEN_HASH_UQ, "sessions", ["token_hash"], unique=True)

    op.create_table(
        "otp_codes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    # (email, created_at): the two lookups this table serves are "the newest live
    # code for this address" and the retention sweep's age scan.
    op.create_index(_OTP_EMAIL_CREATED_IX, "otp_codes", ["email", "created_at"])


def downgrade() -> None:
    op.drop_index(_OTP_EMAIL_CREATED_IX, table_name="otp_codes")
    op.drop_table("otp_codes")
    op.drop_index(_SESSIONS_TOKEN_HASH_UQ, table_name="sessions")
    op.drop_index(_SESSIONS_USER_ID_IX, table_name="sessions")
    op.drop_table("sessions")
