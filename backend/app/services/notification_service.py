"""Per-suite alert notification config (``suite_notifications``).

Stores whether / where / at-what-threshold a suite's run outcomes are delivered.
The Teams webhook URL is token-bearing, so it's written through the SecretStore
and referenced by ``webhook_secret_ref`` (mirrors connection credentials) — never
plaintext in the DB. The Teams publisher reads this config when delivering.

FastAPI-free like the sibling services: takes a ``Session`` (+ a ``SecretStore``
where credentials are involved), returns ORM models, raises ``DataQError``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretNotFoundError, SecretStore
from backend.app.db.models import ALERT_ON_POLICIES, SuiteNotification

log = get_logger(__name__)

# Threshold used for a suite with no config row — preserves the pre-config
# behaviour (alert on warn+). A suite opts into a stricter/looser policy by
# saving a config.
DEFAULT_ALERT_ON = "warn"


class InvalidAlertPolicyError(DataQError):
    """Raised when ``alert_on`` isn't one of the allowed policies."""

    status_code = 422
    code = "alert_policy_invalid"


class InvalidWebhookError(DataQError):
    """Raised when a provided webhook URL isn't an https URL."""

    status_code = 422
    code = "webhook_invalid"


def get_config(session: Session, suite_id: uuid.UUID) -> SuiteNotification | None:
    """The suite's notification config, or None if it has never been saved."""
    return session.scalars(
        select(SuiteNotification).where(SuiteNotification.suite_id == suite_id)
    ).first()


def upsert_config(
    session: Session,
    *,
    suite_id: uuid.UUID,
    enabled: bool,
    alert_on: str,
    webhook: str | None,
    secret_store: SecretStore,
) -> SuiteNotification:
    """Create or update a suite's notification config.

    ``webhook`` is tri-state: ``None`` leaves the stored webhook unchanged, ``""``
    clears it (fall back to the workspace webhook), and a non-empty value is
    written through the SecretStore (the ref is derived from the row id, like a
    connection credential).
    """
    if alert_on not in ALERT_ON_POLICIES:
        raise InvalidAlertPolicyError(
            "invalid alert policy",
            detail={"alert_on": alert_on, "allowed": list(ALERT_ON_POLICIES)},
        )
    if webhook:  # non-empty → must be an https URL (token-bearing, sent server-side)
        if not webhook.startswith("https://"):
            raise InvalidWebhookError("webhook must be an https URL")
    config = get_config(session, suite_id)
    if config is None:
        config = SuiteNotification(suite_id=suite_id, enabled=enabled, alert_on=alert_on)
        session.add(config)
        session.flush()  # assign id for the secret_ref below
    else:
        config.enabled = enabled
        config.alert_on = alert_on

    if webhook is not None:
        if webhook == "":
            config.webhook_secret_ref = None
        else:
            secret_ref = config.webhook_secret_ref or f"suite-notif-{config.id}"
            secret_store.set(secret_ref, webhook)
            config.webhook_secret_ref = secret_ref

    session.commit()
    session.refresh(config)
    log.info("suite_notification_saved", suite_id=str(suite_id), enabled=enabled, alert_on=alert_on)
    return config


def delete_config(session: Session, suite_id: uuid.UUID) -> bool:
    """Delete a suite's config (revert to defaults). Returns whether a row existed."""
    config = get_config(session, suite_id)
    if config is None:
        return False
    session.delete(config)
    session.commit()
    log.info("suite_notification_deleted", suite_id=str(suite_id))
    return True


def resolve_webhook(
    config: SuiteNotification | None,
    *,
    secret_store: SecretStore,
    workspace_secret_name: str | None,
) -> str | None:
    """The webhook URL to deliver to: the per-suite one, else the workspace one.

    Returns ``None`` when neither resolves (delivery is then skipped). A missing
    secret is logged (by ref/name, never the value) and falls through.
    """
    ref = config.webhook_secret_ref if config is not None else None
    if ref:
        try:
            return secret_store.get(ref)
        except SecretNotFoundError:
            log.warning("suite_webhook_unresolved", secret_ref=ref)
    if workspace_secret_name:
        try:
            return secret_store.get(workspace_secret_name)
        except SecretNotFoundError:
            log.warning("teams_webhook_unresolved", secret_name=workspace_secret_name)
    return None
