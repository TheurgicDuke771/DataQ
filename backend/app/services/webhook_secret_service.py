"""Inbound-webhook secret rotation with a grace window (#1701).

Regeneration writes the new value under the provider's configured SecretStore key
and parks the previous value under ``<key>-previous`` with an expiry, so a receiver
accepts either until the operator has updated the provider side. One helper serves
every provider's verifier — an ADF shared secret and an Airflow/dbt HMAC key differ
in how they are checked, not in how they rotate.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretNotFoundError, SecretStore

log = get_logger(__name__)

#: Suffix of the parked-previous key. Charset stays inside Key Vault's `[0-9a-zA-Z-]`.
PREVIOUS_SUFFIX = "-previous"

#: Bytes of entropy in a minted secret (~43 URL-safe chars).
_ENTROPY_BYTES = 32


def previous_key_name(secret_name: str) -> str:
    return f"{secret_name}{PREVIOUS_SUFFIX}"


@dataclass(frozen=True)
class Regeneration:
    """The result of a rotation. ``value`` is returned to the caller ONCE."""

    value: str
    #: `None` when nothing was parked — no previous value existed, or the grace is 0.
    grace_until: datetime | None


def read_previous(secret_store: SecretStore, secret_name: str, *, now: datetime) -> str | None:
    """The parked previous value while it is still inside its grace window.

    `None` for every other case — never parked, expired, or unreadable — because a
    receiver must not treat an unparseable blob as an accepted credential.
    """
    try:
        raw = secret_store.get(previous_key_name(secret_name))
    except SecretNotFoundError:
        return None
    except Exception:
        # A store outage on the OPTIONAL key must not take verification of the
        # current secret down with it.
        log.warning("webhook_previous_secret_unreadable", secret_name=secret_name, exc_info=True)
        return None
    try:
        parked = json.loads(raw)
        value = parked["value"]
        expires_at = datetime.fromisoformat(parked["expires_at"])
    except Exception:
        log.warning("webhook_previous_secret_malformed", secret_name=secret_name)
        return None
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value or expires_at <= now:
        return None
    return value


def acceptable_secrets(
    secret_store: SecretStore, secret_name: str, *, now: datetime | None = None
) -> list[str]:
    """Every value a receiver may accept right now — the current one first, then a
    non-expired previous. Empty means the receiver is not configured at all.
    """
    moment = now or datetime.now(UTC)
    values: list[str] = []
    try:
        current = secret_store.get(secret_name)
    except SecretNotFoundError:
        current = ""
    if current:
        values.append(current)
    previous = read_previous(secret_store, secret_name, now=moment)
    if previous and previous not in values:
        values.append(previous)
    return values


def regenerate(
    secret_store: SecretStore, secret_name: str, *, now: datetime | None = None
) -> Regeneration:
    """Mint a new secret and park the outgoing one for the configured grace window."""
    moment = now or datetime.now(UTC)
    grace_minutes = max(0, get_settings().webhook_secret_grace_minutes)
    try:
        outgoing: str | None = secret_store.get(secret_name)
    except SecretNotFoundError:
        outgoing = None

    grace_until: datetime | None = None
    if outgoing and grace_minutes > 0:
        grace_until = moment + timedelta(minutes=grace_minutes)
        # Park BEFORE the new value lands: a failure here abandons the rotation with
        # the old secret still current, rather than half-applying it.
        secret_store.set(
            previous_key_name(secret_name),
            json.dumps({"value": outgoing, "expires_at": grace_until.isoformat()}),
        )

    value = secrets.token_urlsafe(_ENTROPY_BYTES)
    secret_store.set(secret_name, value)
    log.info(
        "webhook_secret_regenerated",
        secret_name=secret_name,
        grace_until=grace_until.isoformat() if grace_until else None,
    )
    return Regeneration(value=value, grace_until=grace_until)
