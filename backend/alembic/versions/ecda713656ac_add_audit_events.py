"""audit_events: the append-only cross-entity audit log — ADR 0041 phase 1 (#1318)

One additive table. It answers the questions ADR 0020's Type-4 snapshot tables
structurally cannot: who deleted this, who rotated that credential, who granted
that share — because a Type-4 table cascades away with its entity, taking the
delete event with it.

**`entity_id` deliberately has NO foreign key.** An FK leaves two options, both
self-defeating: CASCADE (the audit row dies with the entity — exactly the failure
this table exists to fix) or RESTRICT (the audit log makes deletion impossible).
The in-repo precedent is `check_versions.source_connection_id`, a plain UUID with
the same deliberate no-FK comment. `actor_user_id` DOES carry an FK, with
`ON DELETE SET NULL`, because the event must outlive its actor while the
denormalized `actor_label` keeps it legible.

**The REVOKE is a guard against ACCIDENTAL in-app mutation, not tamper-resistance,
and this migration does not pretend otherwise.** The deployed stack creates the
database `OWNER dataq_app` and hands the migrate job the same `DATABASE_URL` as
the api and worker, so Alembic creates this table owned by `dataq_app` — which can
`GRANT` the revoked privileges straight back in one statement, or `TRUNCATE`/`DROP`
the table regardless of any grant. What the REVOKE buys is that a stray ORM
`session.delete` or a careless bulk `UPDATE` fails loudly instead of silently
rewriting history. Splitting the role to harden it is explicitly rejected (ADR
0041 §2.7): a second, less-trusted role in the `dataq` database is precisely what
the project's standing Postgres constraint forbids, because the referential-
integrity check runs implicit casts as the referenced table's owner. Real
tamper-evidence needs an external cryptographic anchor and belongs to #431.

**The REVOKE also revokes DELETE from the retention sweep, and that is a
deliberate, verified consequence rather than an oversight.** Proven live in the
production shape (database `OWNER dataq_app`, migrated as `dataq_app`): after this
migration the role holds INSERT/SELECT on `audit_events` and neither UPDATE nor
DELETE, while every other table keeps all seven privileges — and a `DELETE FROM
audit_events` as `dataq_app` is refused with `permission denied`. ADR 0041 §2.7
requires the retention sweep to run as `dataq_app` (adding a second database role
is forbidden by the standing Postgres RI constraint), so the sweep — which lands
with the read surface, not here — must re-grant DELETE for the duration of its own
statement and revoke it again in the same transaction. That is the honest shape:
the owner can always do this (which is why §2.7 says the REVOKE is not
tamper-resistance), and the guard still does the job it is actually for, because a
stray ORM `session.delete` or a careless bulk `UPDATE` from the request path issues
no GRANT and therefore still fails loudly.

The REVOKE is written to be a no-op wherever the role does not exist (local dev,
CI, and any deployment that named its role differently) rather than failing the
migration — the table is the deliverable, the grant is a hardening step on top of
it, and a missing role must not block a deploy.

Purely additive and backward-compatible (CLAUDE.md migration rules): a new table
only; no existing table, column or constraint is touched, so code deployed BEFORE
this migration keeps working unmodified, and code deployed AFTER gets the table
the migrate job created ahead of the container roll.

Tested up + down locally. Rollback: `downgrade()` drops the table and its
indexes. It is lossy by nature — the recorded events go with it — which is
inherent to rolling back the feature that records them, and safe for the
deployment, since nothing else references the table.

Revision ID: ecda713656ac
Revises: f58b4bff54f7
Create Date: 2026-08-17 00:00:00.000000+00:00

"""

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

    A second hand-written copy of the only thing standing between the audit log
    and a silent rewrite is the "guard at one door and not its sibling" shape, and
    that divergence is invisible until it matters. The test creates a role, grants
    it the four DML privileges, runs this exact string, and asserts UPDATE/DELETE
    are gone while INSERT/SELECT survive.

    Wrapped in a `DO` block so a deployment without this role (local dev, CI, or
    any deployment that named its role differently) is a no-op rather than a
    failed migration — the table is the deliverable; the grant is hardening on top
    of it.

    S608/B608 are suppressed below: there is no user input here and none is
    possible. The only caller passes a module constant, and a Postgres role name
    is an IDENTIFIER — it cannot be bound as a parameter in a REVOKE at all, so
    string construction is the only available form.
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
        # `op.f(...)` with the full conventional name — see f58b4bff54f7's note:
        # a bare name resolves differently depending on whether the migration
        # context carries `target_metadata`, which would make `downgrade()`
        # unable to find its own constraint.
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
    # The class-scoped feed. `action_class` leads so a retention sweep or a
    # read-only `access` feed never scans the other class's rows — phase 2's read
    # volume is expected to dwarf phase 1's, and this index is what lets the two
    # classes share one table without paying for each other.
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
    # Append-only guard. DO block so a deployment without this role (local dev,
    # CI) is a no-op rather than a failed migration — the table is the
    # deliverable; the grant is hardening on top of it.
    op.execute(revoke_statement(_APP_ROLE))


def downgrade() -> None:
    op.drop_index("ix_audit_events_actor", table_name="audit_events")
    op.drop_index("ix_audit_events_class_occurred", table_name="audit_events")
    op.drop_index("ix_audit_events_entity", table_name="audit_events")
    op.drop_table("audit_events")
