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
2. **Ownership** — the name must not be referenced by any row. Every reference site
   is registered in `_OWNER_COLUMNS` / `_OWNER_JSON_PATHS`, and a schema-introspection
   test fails the build when a new column joins the club (the `_SWEEP_REFERENCE_GUARDS`
   idea from #770). Writing that registry is where this change nearly went wrong: the
   obvious two entries miss `SuiteNotification.slack_webhook_secret_ref` (Slack and
   Teams are separate refs on one row) and `catalog_secret_name`, which lives in
   `Connection.config` JSONB and so is invisible to any column-level audit.
3. **Age** — the secret must be older than the grace period, read from the STORE's
   own creation time. An unknown age counts as too young. This is what stops the
   sweep racing a connection-create that has written its secret but not committed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretInfo, SecretStore
from backend.app.db.models import Connection, SuiteNotification

log = get_logger(__name__)

# Only names DataQ mints are ever candidates. `conn-` covers connection credentials
# (`conn-<type>-<qualifier>-<env>-<shortid>`); `suite-notif` covers the notification
# webhook secrets, including the `suite-notif-slack-` variant. Anything else in the
# vault belongs to someone else — an operator sharing the mount, another app — and is
# out of scope by construction, not by luck.
_DATAQ_PREFIXES: tuple[str, ...] = ("conn-", "suite-notif")


@dataclass(frozen=True)
class OrphanSweepResult:
    """What one sweep found and did. `purged` is always <= `orphans`."""

    scanned: int
    orphans: list[str]
    purged: list[str]
    # Orphans held back solely because they are inside the grace period (or their age
    # could not be read). Reported separately so "nothing was purged" can be
    # distinguished from "nothing was old enough" — otherwise a misconfigured grace
    # period looks exactly like a clean vault.
    too_young: list[str]


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
# not in a column of its own. It is also never written by `connection_service` — an
# operator provisions it out of band — so it is invisible to every "what did we write"
# audit as well. Prefix scoping alone would not save it: an operator is free to name it
# `conn-…`.
_OWNER_JSON_PATHS = ((Connection.config, "catalog_secret_name"),)


def _owned_secret_refs(session: Session) -> set[str]:
    """Every secret name any row currently references.

    Read as ONE set across all owner sites before comparing, so a secret referenced
    by any of them survives.
    """
    refs: set[str] = set()
    for column in _OWNER_COLUMNS:
        refs.update(
            value for value in session.scalars(select(column).where(column.isnot(None))) if value
        )
    for json_column, key in _OWNER_JSON_PATHS:
        expression = json_column[key].astext
        refs.update(
            value
            for value in session.scalars(select(expression).where(expression.isnot(None)))
            if value
        )
    return refs


def find_orphan_secrets(
    session: Session,
    *,
    secrets: Sequence[SecretInfo],
    grace: timedelta,
    now: datetime | None = None,
) -> tuple[list[str], list[str]]:
    """Split DataQ-owned store entries into (purgeable orphans, too-young orphans).

    Pure and side-effect-free so the dangerous decision can be tested exhaustively
    without a store or a delete.
    """
    moment = now or datetime.now(UTC)
    owned = _owned_secret_refs(session)
    orphans: list[str] = []
    too_young: list[str] = []
    for info in secrets:
        if not info.name.startswith(_DATAQ_PREFIXES) or info.name in owned:
            continue
        # Unknown age → too young. A store that cannot date its secrets can never
        # have them purged, which is the correct default for a destructive action.
        if info.created_at is None or moment - info.created_at < grace:
            too_young.append(info.name)
        else:
            orphans.append(info.name)
    return orphans, too_young


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
    """
    if grace_days <= 0:
        return OrphanSweepResult(scanned=0, orphans=[], purged=[], too_young=[])
    lister = getattr(store, "list_secrets", None)
    if not callable(lister):
        log.info("secret_orphan_sweep_skipped", reason="store cannot enumerate secrets")
        return OrphanSweepResult(scanned=0, orphans=[], purged=[], too_young=[])

    # Deliberately NOT caught: `SecretStoreUnavailableError` propagates and fails the
    # task. A vault that cannot be listed must not be reported as "no orphans found",
    # and must certainly not lead to deletions — the #954 masquerade, applied to a
    # destructive path.
    secrets = cast("list[SecretInfo]", lister())
    orphans, too_young = find_orphan_secrets(
        session, secrets=secrets, grace=timedelta(days=grace_days), now=now
    )

    purged: list[str] = []
    if purge:
        for name in orphans:
            # `delete` is fail-soft by contract, so a store hiccup skips one secret
            # rather than aborting the sweep. Recorded per name so the count reflects
            # what was actually deleted, not what was intended.
            store.delete(name)
            purged.append(name)

    if orphans or too_young:
        log.warning(
            "secret_orphan_sweep",
            scanned=len(secrets),
            # Names only — a secret NAME is a non-secret identifier (it is already
            # stored in `connections.secret_ref` and served by the read API). Values
            # are never fetched by this path at all.
            orphans=len(orphans),
            too_young=len(too_young),
            purged=len(purged),
            purge_enabled=purge,
            orphan_names=sorted(orphans),
        )
    return OrphanSweepResult(
        scanned=len(secrets), orphans=orphans, purged=purged, too_young=too_young
    )
