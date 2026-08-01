"""users: nullable aad_object_id + unique index on lower(email) (ADR 0032 decision 6, #735 step 1)

Step 1 of the two-step identity migration for email OTP sign-in
(ADR `0032-email-otp-signin.md`, **decision 6** — "one user row per normalized
email"). This revision ships **schema only**: it relaxes `users.aad_object_id`
to nullable and adds a unique index on `lower(email)`. The code that upserts /
links users **by email** ships separately, after this migration is deployed
(#734) — CLAUDE.md §6 "backward-compatible migrations only".

Why each half:

* `aad_object_id` NULL — an OTP-provisioned user has no Azure AD identity. The
  existing unique constraint (`uq_users_aad_object_id`) is **kept**: Postgres
  treats NULLs as distinct in a unique constraint, so any number of OTP users
  can coexist with NULL while AAD users stay one-row-per-oid.
* `uq_users_email_lower` — email becomes the identity key that AAD and OTP
  sign-ins join on, so it must be unique *case-insensitively*. Emails are stored
  verbatim from the JWT claims today (`core/auth.py` `_extract_claims`), so
  `Foo@X.com` and `foo@x.com` could already be two rows. `lower(...)` is the same
  normalization `Settings.is_admin_email` applies to `WORKSPACE_ADMIN_EMAILS`
  (`core/config.py` — strip + lower), i.e. one normalization rule across the
  identity surface. (The index cannot express the *strip* half; leading/trailing
  whitespace in a stored address is not something any writer produces, and the
  application-level rule remains `is_admin_email`'s.)

## MANDATORY PRE-DEPLOY: duplicate-email audit

`CREATE UNIQUE INDEX` fails if the table already holds two rows whose emails
differ only in case. **Run this against every live database before deploying
this revision:**

    SELECT lower(email) AS normalized_email, count(*) AS rows
    FROM users
    GROUP BY 1
    HAVING count(*) > 1
    ORDER BY rows DESC;

Zero rows → deploy. Any rows → **do not deploy yet**: resolve the duplicates
first (decide which row is the real human — the one owning suites/PATs/shares —
and re-point or remove the other; see ADR 0032 decision 6's linking rule).

If the audit is skipped and duplicates exist, the index creation raises
`UniqueViolation`, this migration's transaction rolls back whole
(`alembic/env.py` sets `transaction_per_migration=True`), the revision is NOT
stamped, and the schema is left exactly as it was. That abort is the designed
behaviour — a loud, clean failure in the migrate job is strictly better than a
silently-merged identity.

Lock note: plain `CREATE UNIQUE INDEX` (no `CONCURRENTLY`) inside the migration
transaction — `users` is tiny (tens of rows in the largest deployment we know
of), so the SHARE lock is sub-second, and `CONCURRENTLY` cannot report a
duplicate-email conflict as a clean transactional abort. Precedent + rationale:
`1a2b3c4d5e6f_lineage_edges_nullable_connection.py`.

## Downgrade — deliberately refuses rather than deleting users

The precedent revision (`1a2b3c4d5e6f`) DELETEs the rows the old schema cannot
represent. That is right for `lineage_edges` (derived data, re-derivable by a
refresh) and **wrong here**. Deleting a NULL-`aad_object_id` user would:

* CASCADE-delete their `api_keys` (PATs — unrecoverable verifier secrets) and
  their `shares` (another human's access grants silently revoked), and
* fail outright anyway wherever that user is the non-nullable, NO-ACTION parent
  of `connections.created_by`, `suites.created_by` or `schedules.created_by` —
  a raw FK violation mid-downgrade, i.e. a half-applied rollback.

So `downgrade()` **raises with an actionable message** when NULL-aad users
exist, and does nothing else (checked before any DDL, so the refusal is a clean
no-op). Rollback plan:

1. Roll the application image back first (code stops minting OTP users).
2. Run the audit query printed in the error to see the affected users.
3. Decide per user: re-provision them via AAD (sets `aad_object_id`), or delete
   them **explicitly** having accepted the PAT/share loss.
4. Re-run `alembic downgrade`; with no NULL-aad rows it completes.

With no OTP users yet provisioned — the state during and immediately after this
step-1 deploy, since the upsert code is not shipped — the downgrade is
unconditionally clean.

Revision ID: 7d25617cfaf0
Revises: 70a054d4c469
Create Date: 2026-08-01 00:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7d25617cfaf0"
down_revision: str | None = "70a054d4c469"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Must match `User.__table_args__` in `backend/app/db/models.py` exactly — the
# model declares the same expression index so `create_all` test databases and
# production agree (the #990 parity check compares the two).
_EMAIL_LOWER_UQ = "uq_users_email_lower"

# The two statements this revision quotes back to the operator. Constants, not
# inline f-strings: nothing is interpolated into them (the downgrade message only
# concatenates), which is both true and what keeps the SQL-injection linter from
# reading a help message as query construction.
_NULL_AAD_COUNT_SQL = "SELECT count(*) FROM users WHERE aad_object_id IS NULL"
_NULL_AAD_INSPECT_SQL = "SELECT id, email, created_at FROM users WHERE aad_object_id IS NULL;"


def upgrade() -> None:
    op.alter_column("users", "aad_object_id", existing_type=sa.String(64), nullable=True)
    op.create_index(_EMAIL_LOWER_UQ, "users", [sa.text("lower(email)")], unique=True)


def downgrade() -> None:
    # Checked BEFORE any DDL so the refusal leaves the schema untouched. See the
    # module docstring for why this refuses instead of deleting.
    orphans = op.get_bind().execute(sa.text(_NULL_AAD_COUNT_SQL)).scalar_one()
    if orphans:
        raise RuntimeError(
            f"{orphans} user row(s) have a NULL aad_object_id (OTP-provisioned identities) "
            "and cannot be represented by the pre-#735 schema. Downgrading would CASCADE-"
            "delete their PATs and access shares — and fail outright where they own a "
            "connection/suite/schedule — so this migration refuses. Inspect them with:\n  "
            + _NULL_AAD_INSPECT_SQL
            + "\nthen either re-provision each via Azure AD (which sets aad_object_id) or "
            "delete them explicitly, accepting the PAT/share loss, and re-run the downgrade."
        )
    op.drop_index(_EMAIL_LOWER_UQ, table_name="users")
    op.alter_column("users", "aad_object_id", existing_type=sa.String(64), nullable=False)
