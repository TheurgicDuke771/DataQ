"""sessions + otp_codes: the email-OTP verifier stores (ADR 0032 decisions 3+4, #734)

Two new tables, **purely additive** — nothing existing is altered, so this is
backward-compatible by construction: code running before the migration never
touches either table, and code running after it needs both.

* ``sessions`` — the browser sign-in credential (ADR 0032 decision 3). Copies the
  ``api_keys`` shape exactly: uuid pk, owner FK ``ondelete=CASCADE`` (a deleted
  user's sessions die with them), a SHA-256 hex ``token_hash`` behind a **unique
  INDEX** (not a unique constraint — see below), ``expires_at`` (fixed horizon,
  no refresh pair) and ``revoked_at`` (logout).
* ``otp_codes`` — the one-time codes (ADR 0032 decision 4). Keyed on the
  *normalized* email and deliberately **not** FK'd to ``users``: a code is
  requested before any user row need exist, and an ineligible address must be
  processable without provisioning an identity for it.

## Why a unique INDEX and not a unique constraint on ``token_hash``

The same deliberate choice ``api_keys`` already carries (``uq_api_keys_key_hash``,
model comment in ``db/models.py``): the two are interchangeable for *enforcement*
but not for *identity*. A ``UniqueConstraint`` is auto-named
``sessions_token_hash_key`` by ``create_all`` in every test database while a
migration-created constraint would carry whatever we named it — and code that keys
on a constraint name (``connection_service._conflict_from_integrity_error`` does,
for connections) would then behave differently in tests than in production. The
#990 parity check compares the model's declaration against this file, so both
sides declare the same ``Index(..., unique=True)``.

## Lock note

Both statements are ``CREATE TABLE`` on tables that do not yet exist, so no
existing object is locked and no running query is blocked. This is the ordinary
additive case — unlike ``7d25617cfaf0`` (the ``users`` ALTER), which needed a
lock-duration argument.

## Tested up + down locally

    alembic upgrade head    # 7d25617cfaf0 -> d0b7ecd7b675
    alembic downgrade -1    # d0b7ecd7b675 -> 7d25617cfaf0
    alembic upgrade head    # re-applied clean

## Rollback plan

``downgrade()`` drops both tables. Unlike ``7d25617cfaf0``'s downgrade (which
refuses, because it would CASCADE-delete PATs and shares), dropping these is safe
and complete: a ``sessions`` row is a *revocable* credential, so losing it logs
users out — the same outcome as the ``AUTH_SESSION_TTL_HOURS`` expiry they were
going to hit anyway — and an ``otp_codes`` row is a 10-minute-lived artefact. No
other table references either, so no FK is orphaned. Order of operations for a
rollback: roll the application image back first (so nothing mints sessions), then
run the downgrade; users on the old image re-authenticate via Azure AD or the OTP
flow of the newer image, never a half-state.

Revision ID: d0b7ecd7b675
Revises: 7d25617cfaf0
Create Date: 2026-08-01 00:00:00.000000+00:00

"""

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
# `backend/app/db/models.py` exactly, so a `create_all` test database and production
# carry the same objects under the same names (#990 parity check).
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
