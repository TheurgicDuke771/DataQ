"""Reconcile the secret store against the rows that should own its secrets (#1059).

A credential write is **not** part of the Postgres transaction that creates the row
referencing it. `connection_service.create_connection` flushes, writes the secret,
then commits — so any failure after the write (`record_connection_version`, the
commit itself, a deadlock, a dropped connection) rolls the row back and leaves the
credential behind, unreferenced and permanent. `update_connection` and the
notification-webhook path have the same shape, and it is not backend-specific: Key
Vault, OpenBao and the old Redis store all behaved this way. It only became
*visible* with a local vault that is easy to enumerate.

Nothing else cleans these up. `SecretStore.delete` (#372) runs only on an explicit
entity delete, and the credential-expiry sweep (#838) is driven off connection rows,
so an orphan is invisible to it too. ADR 0039 §7 chose a metadata purge over a soft
delete precisely because "leaving a recoverable warehouse credential behind a deleted
entity is the wrong default" — the delete path was hardened and the create path was
not.

**This sweep reports by default and deletes only when explicitly configured.** That
asymmetry is deliberate: the thing being deleted is a live warehouse credential, and
the cost of a wrong delete (a broken production connection, with the credential
unrecoverable once purged) is far higher than the cost of a secret lingering another
week. Detection is what makes the problem visible; deletion is a decision an operator
takes with the numbers in front of them.

Three independent guards stand between a secret and deletion:

1. **Prefix scoping** — only names DataQ itself mints are candidates. An operator's
   own secrets sharing the vault are never touched.
2. **Ownership** — the name must not be referenced by any row OR by `Settings`. Every site
   is registered below, and a schema-introspection test over EVERY mapped table
   fails the build when a new secret-name column lands unregistered (the
   `_SWEEP_REFERENCE_GUARDS` idea from #770). Writing that registry is where this
   change kept going wrong, in three different ways worth naming: Slack and Teams
   are SEPARATE refs on one row (#633); `*_secret_name` keys live in
   `Connection.config` JSONB, invisible to any column-level audit, and are matched
   by CONVENTION because `connection_service` resolves them generically; and six
   workspace-level names live in `Settings` and are owned by no row at all.
3. **Age** — the secret must be older than the grace period, read from the STORE's
   own creation time. This is what stops the sweep racing a connection-create that
   has written its secret but not yet committed. An unknown age never purges, and
   is reported in its OWN bucket rather than as "too young", because for OpenBao it
   usually means a denied metadata read — a permissions gap must not render as
   "everything here is recent".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import func, select, true
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretInfo, SecretStore
from backend.app.core.timeutil import as_utc
from backend.app.db.models import Connection, SuiteNotification

log = get_logger(__name__)

# Only names DataQ mints are ever candidates. `conn-` covers connection credentials
# (`conn-<type>-<qualifier>-<env>-<shortid>`); `suite-notif-` covers the notification
# webhook secrets, including the `suite-notif-slack-` variant. Both carry the trailing
# separator every mint site uses (`core/secret_names.py`, `notification_service`), so
# the prefix cannot widen to a neighbouring convention like `suite-notification-*`.
# Anything else in the vault belongs to someone else and is out of scope by
# construction, not by luck.
#
# NOTE this is a per-PRODUCT prefix, not a per-INSTALL one — see `sweep_orphan_secrets`
# for the shared-mount constraint that follows from it.
_DATAQ_PREFIXES: tuple[str, ...] = ("conn-", "suite-notif-")

# The convention `connection_service._extra_secrets` follows when resolving extra
# credentials out of `Connection.config`: ANY key ending in this suffix names a
# SecretStore entry. Registering the one instance that exists today
# (`catalog_secret_name`) would leave the next connection type that adds one silently
# purgeable, so the scan follows the convention rather than an instance.
_CONFIG_KEY_SUFFIX = "_secret_name"


@dataclass(frozen=True)
class OrphanSweepResult:
    """What one sweep found and did."""

    # Every entry the store returned, INCLUDING names DataQ does not own — this is a
    # store-side total, not a count of DataQ secrets, and reads differently on a
    # shared mount.
    scanned: int
    orphans: list[str]
    # Names `delete` was called for. `delete` is fail-soft by contract (it swallows
    # and logs), so this is what was ATTEMPTED; the store cannot tell us what
    # succeeded without changing that contract, and an honest name beats a comment
    # claiming otherwise.
    purge_attempted: list[str]
    # Unowned, but inside the grace period. Distinguished from `unknown_age` below so
    # a misconfigured grace period cannot look like a clean vault.
    too_young: list[str]
    # Unowned, and the store could not date them. Deliberately NOT folded into
    # `too_young`: for OpenBao a `None` age is what a DENIED metadata read looks
    # like, so conflating them would render a permissions gap as "nothing to do,
    # everything is recent" — an outage reported as a state, which is the #1056 /
    # #954 lesson one level up. These are never purged either way.
    unknown_age: list[str]


# ── The reference registry ────────────────────────────────────────────────────
# Every place in the schema that holds a `SecretStore` name must appear here. A
# missing entry does not fail loudly — it makes live credentials look unowned, i.e.
# it silently arms a delete. `test_every_secret_ref_column_is_registered`
# introspects the models and fails the build when a new `*secret_ref*` column lands
# without a row here.
_OWNER_COLUMNS = (
    Connection.secret_ref,
    SuiteNotification.webhook_secret_ref,
    # Easy to miss: Teams and Slack are SEPARATE refs on the same row (#633).
    SuiteNotification.slack_webhook_secret_ref,
)

# JSONB-held refs, which no column-level scan can see. `IcebergConfig.catalog_secret_name`
# names a SecretStore entry holding the SQL-catalog password (#754/#826 moved it out of
# `catalog_uri` for exactly the right reasons) and lives inside `Connection.config`,
# not in a column of its own — so it needs THIS registry entry even though
# `connection_service.create_connection`/`update_connection` (#1181) write and rotate it
# exactly like the primary `secret_ref`, and `delete_connection` deletes it on the same
# best-effort terms. What still makes it reachable only here, not by a column-level scan:
# it is a value INSIDE a JSONB blob, not a column of its own, so `test_every_secret_ref_
# column_is_registered`'s column introspection cannot see it — and this module's own
# top-of-file rationale (a credential write that is not part of the owning row's DB
# transaction) still applies to it precisely as it does to `secret_ref`: a crash between
# `_write_extra_secret`'s store write and the enclosing commit leaves a real, live,
# unreferenced credential exactly like the create-path race this whole sweep exists for.
# Prefix scoping alone would not save it either way: an operator may name it `conn-…`.
#
# Scanned by CONVENTION (any `*_secret_name` key), not by listing the one key that
# exists today, because `connection_service._extra_secrets` resolves them generically
# and says so: "no branching on connection.type … no matter how many credentials a
# future type needs". A registry that named `catalog_secret_name` alone would silently
# arm a delete on the next type that adds one.
_JSON_CONFIG_COLUMNS = (Connection.config,)


def _settings_owned_names() -> set[str]:
    """Workspace-level secret names that live in `Settings`, owned by NO row.

    The webhook-verification and alerting credentials (`*_WEBHOOK_SECRET_NAME`,
    `TEAMS_/SLACK_WEBHOOK_SECRET_NAME`, `EMAIL_PASSWORD_SECRET_NAME`) are real
    SecretStore entries configured per deployment. Being row-less is exactly what
    makes them dangerous here: without this, only the prefix filter stands between
    them and a purge — and three of them are operator-named with blank defaults, so
    calling the workspace Teams webhook `suite-notif-teams-workspace` (a natural
    choice, since DataQ mints `suite-notif-*` itself) would get it deleted.
    """
    settings = get_settings()
    return {
        name.strip()
        for name in (
            settings.adf_webhook_secret_name,
            settings.airflow_webhook_secret_name,
            settings.dbt_webhook_secret_name,
            settings.teams_webhook_secret_name,
            settings.slack_webhook_secret_name,
            settings.email_password_secret_name,
        )
        if name and name.strip()
    }


def _owned_secret_refs(session: Session) -> set[str]:
    """Every secret name anything currently references — rows AND config.

    Read as ONE set across all owner sites before comparing, so a secret referenced
    by any of them survives.
    """
    refs: set[str] = _settings_owned_names()
    for column in _OWNER_COLUMNS:
        refs.update(
            value for value in session.scalars(select(column).where(column.isnot(None))) if value
        )
    for json_column in _JSON_CONFIG_COLUMNS:
        # Every `*_secret_name` value in the JSONB doc, whatever the key is called.
        # `jsonb_each_text` expands the object so the LIKE applies to the KEY — a
        # fixed `config['catalog_secret_name']` lookup could not do that.
        #
        # LATERAL, joined against the owning table: the set-returning function takes
        # that table's column as its argument, so without the join `connections.config`
        # is simply not in scope and Postgres rejects the statement.
        table = json_column.parent.class_.__table__
        pair = func.jsonb_each_text(json_column).table_valued("key", "value").lateral("cfg")
        statement = (
            select(pair.c.value)
            .select_from(table.join(pair, true()))
            .where(pair.c.key.like(f"%{_CONFIG_KEY_SUFFIX}"), pair.c.value.isnot(None))
            # `jsonb_each_text` ERRORS on a non-object, so a config that is a JSON
            # scalar/array/null would fail the whole sweep rather than yield nothing.
            .where(func.jsonb_typeof(json_column) == "object")
        )
        refs.update(value for value in session.scalars(statement) if value)
    return refs


def find_orphan_secrets(
    session: Session,
    *,
    secrets: Sequence[SecretInfo],
    grace: timedelta,
    now: datetime | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """Split DataQ-owned store entries into (orphans, too_young, unknown_age).

    Pure and side-effect-free so the dangerous decision can be tested exhaustively
    without a store or a delete.

    `unknown_age` is a THIRD bucket, not a flavour of `too_young`, because the two
    have different causes and different fixes: too-young is the sweep working as
    intended, unknown-age usually means the store would not tell us — for OpenBao,
    a denied `metadata` read looks exactly like this. Folding them together would
    report a permissions gap as "everything here is recent", which is an outage
    rendered as a state. Neither bucket is ever purged.
    """
    moment = now or datetime.now(UTC)
    owned = _owned_secret_refs(session)
    orphans: list[str] = []
    too_young: list[str] = []
    unknown_age: list[str] = []
    for info in secrets:
        if not info.name.startswith(_DATAQ_PREFIXES) or info.name in owned:
            continue
        if info.created_at is None:
            unknown_age.append(info.name)
        # A future-dated secret yields a negative delta, which is < grace, so clock
        # skew on the store side also lands in `too_young` rather than purging.
        elif moment - as_utc(info.created_at) < grace:
            too_young.append(info.name)
        else:
            orphans.append(info.name)
    return orphans, too_young, unknown_age


def sweep_orphan_secrets(
    session: Session,
    *,
    store: SecretStore,
    grace_days: int,
    purge: bool,
    now: datetime | None = None,
) -> OrphanSweepResult:
    """Find (and optionally purge) store entries no row references.

    `purge=False` — the default posture — reports without deleting. `grace_days <= 0`
    disables the sweep entirely, mirroring the other beat janitors' off-switch.

    A store that cannot enumerate itself (`EnvSecretStore`, and every test double)
    is skipped rather than treated as empty: an empty listing would mean "every
    secret is an orphan", so the absence of `list_secrets` must never be read as a
    result. Same duck-typing rationale as `close()` — see `secrets.py`.

    **Shared-mount constraint.** The `conn-` / `suite-notif-` prefixes identify the
    PRODUCT, not the INSTALL, and ADR 0039 explicitly supports pointing DataQ at an
    operator's existing Vault. Two DataQ deployments sharing one mount — staging and
    prod is the obvious case — therefore each see the other's entries as unowned. With
    `purge` on, they would delete each other's live credentials. Give each deployment
    its own mount (`OPENBAO_MOUNT`) or its own vault before enabling the purge; this
    is a documented constraint rather than an enforced one because the store has no
    notion of install identity to key on.
    """
    empty = OrphanSweepResult(
        scanned=0, orphans=[], purge_attempted=[], too_young=[], unknown_age=[]
    )
    if grace_days <= 0:
        return empty
    lister = getattr(store, "list_secrets", None)
    if not callable(lister):
        log.info("secret_orphan_sweep_skipped", reason="store cannot enumerate secrets")
        return empty

    # Deliberately NOT caught: `SecretStoreUnavailableError` propagates and fails the
    # task rather than reporting "no orphans found" — the #954 masquerade, applied to
    # a destructive path. (For THIS sweep a short listing under-reports orphans, which
    # is the safe direction; the incompleteness that would over-delete is on the DB
    # side of the join, which is what the ownership registry above guards.)
    secrets = cast("list[SecretInfo]", lister())
    orphans, too_young, unknown_age = find_orphan_secrets(
        session, secrets=secrets, grace=timedelta(days=grace_days), now=now
    )

    purge_attempted: list[str] = []
    if purge:
        for name in orphans:
            # `delete` is fail-soft BY CONTRACT — it swallows and logs — so this
            # records the attempt, not the outcome. The store cannot report success
            # without changing that contract, and naming the field for what it
            # actually holds beats a comment claiming more than the code delivers.
            store.delete(name)
            purge_attempted.append(name)

    if orphans or too_young or unknown_age:
        log.warning(
            "secret_orphan_sweep",
            # Store-side total, including entries DataQ does not own.
            scanned=len(secrets),
            orphans=len(orphans),
            too_young=len(too_young),
            # Its own field: a non-zero count here on OpenBao usually means the
            # token lacks `read` on `<mount>/metadata/*`, not that the secrets are new.
            unknown_age=len(unknown_age),
            purge_attempted=len(purge_attempted),
            purge_enabled=purge,
            # Names only — a secret NAME is a non-secret identifier (already stored in
            # `connections.secret_ref` and served by the read API). Values are never
            # fetched by this path at all.
            orphan_names=sorted(orphans),
        )
    return OrphanSweepResult(
        scanned=len(secrets),
        orphans=orphans,
        purge_attempted=purge_attempted,
        too_young=too_young,
        unknown_age=unknown_age,
    )
