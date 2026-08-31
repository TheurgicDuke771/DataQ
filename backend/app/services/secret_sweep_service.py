"""Reconcile the secret store against the rows that should own its secrets (#1059)."""

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
from backend.app.db.models import Connection, LlmSetting, NotificationChannel, SuiteNotification

log = get_logger(__name__)

# Only names DataQ mints are ever candidates.
_DATAQ_PREFIXES: tuple[str, ...] = ("conn-", "suite-notif-", "channel-")

# The convention `connection_service._extra_secrets` follows when resolving extra credentials out of
# `Connection.config`: ANY key ending in this suffix names a SecretStore entry.
_CONFIG_KEY_SUFFIX = "_secret_name"


@dataclass(frozen=True)
class OrphanSweepResult:
    """What one sweep found and did."""

    # Every entry the store returned, INCLUDING names DataQ does not own — this is a store-side
    # total, not a count of DataQ secrets, and reads differently on a shared mount.
    scanned: int
    orphans: list[str]
    # Names `delete` was called for.
    purge_attempted: list[str]
    # Unowned, but inside the grace period. Distinguished from `unknown_age` below so
    # a misconfigured grace period cannot look like a clean vault.
    too_young: list[str]
    # Unowned, and the store could not date them.
    unknown_age: list[str]


# ── The reference registry ──────────────────────────────────────────────────── Every place in the
# schema that holds a `SecretStore` name must appear here.
_OWNER_COLUMNS = (
    Connection.secret_ref,
    SuiteNotification.webhook_secret_ref,
    # Easy to miss: Teams and Slack are SEPARATE refs on the same row (#633).
    SuiteNotification.slack_webhook_secret_ref,
    # The workspace LLM credential (ADR 0042) — a purge here kills the provider.
    LlmSetting.api_key_secret_ref,
    # Reusable channels (#1514) — the exact "two refs, one row" trap above, but at
    # the table level: this is a THIRD place a webhook secret_ref column lives.
    NotificationChannel.webhook_secret_ref,
    # The generic webhook's HMAC signing key (#1662) — a FOURTH ref column on the
    # same row; `webhook_url` beside it is NOT a secret (it's the destination, not
    # the credential) and deliberately does not belong in this registry.
    NotificationChannel.hmac_secret_ref,
)

# JSONB-held refs, which no column-level scan can see.
_JSON_CONFIG_COLUMNS = (Connection.config,)


def _settings_owned_names() -> set[str]:
    """Workspace-level secret names that live in `Settings`, owned by NO row."""
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
    """Every secret name anything currently references — rows AND config."""
    refs: set[str] = _settings_owned_names()
    for column in _OWNER_COLUMNS:
        refs.update(
            value for value in session.scalars(select(column).where(column.isnot(None))) if value
        )
    for json_column in _JSON_CONFIG_COLUMNS:
        # Every `*_secret_name` value in the JSONB doc, whatever the key is called.
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
    """Split DataQ-owned store entries into (orphans, too_young, unknown_age)."""
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
    """Find (and optionally purge) store entries no row references."""
    empty = OrphanSweepResult(
        scanned=0, orphans=[], purge_attempted=[], too_young=[], unknown_age=[]
    )
    if grace_days <= 0:
        return empty
    lister = getattr(store, "list_secrets", None)
    if not callable(lister):
        log.info("secret_orphan_sweep_skipped", reason="store cannot enumerate secrets")
        return empty

    # Deliberately NOT caught: `SecretStoreUnavailableError` propagates and fails the task rather
    # than reporting "no orphans found" — the #954 masquerade, applied to a destructive path.
    secrets = cast("list[SecretInfo]", lister())
    orphans, too_young, unknown_age = find_orphan_secrets(
        session, secrets=secrets, grace=timedelta(days=grace_days), now=now
    )

    purge_attempted: list[str] = []
    if purge:
        for name in orphans:
            # `delete` is fail-soft BY CONTRACT — it swallows and logs — so this records the
            # attempt, not the outcome.
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
            # `connections.secret_ref` and served by the read API).
            orphan_names=sorted(orphans),
        )
    return OrphanSweepResult(
        scanned=len(secrets),
        orphans=orphans,
        purge_attempted=purge_attempted,
        too_young=too_young,
        unknown_age=unknown_age,
    )
