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
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
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
    """Raised when a webhook URL isn't https or targets a non-allowlisted host."""

    status_code = 422
    code = "webhook_invalid"


class InvalidRecipientsError(DataQError):
    """Raised when a per-suite email recipient list is malformed (#633)."""

    status_code = 422
    code = "recipients_invalid"


def _hosts_from(raw: str) -> tuple[str, ...]:
    return tuple(host.strip().lower() for host in raw.split(",") if host.strip())


def allowed_webhook_hosts() -> tuple[str, ...]:
    """Host suffixes a per-suite **Teams** webhook URL may target (SSRF allowlist).

    Sourced from the ``teams_webhook_allowed_hosts`` setting (comma-separated).
    """
    return _hosts_from(get_settings().teams_webhook_allowed_hosts)


def allowed_slack_hosts() -> tuple[str, ...]:
    """Host suffixes a per-suite **Slack** webhook URL may target (#633).

    Sourced from the ``slack_webhook_allowed_hosts`` setting (default hooks.slack.com).
    """
    return _hosts_from(get_settings().slack_webhook_allowed_hosts)


def _host_allowed(url: str, hosts: tuple[str, ...]) -> bool:
    """True iff ``url`` is an https URL whose host is within ``hosts`` (exact or
    subdomain). SSRF guard: the webhook is user-supplied and POSTed server-side."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in hosts)


def is_allowed_webhook(url: str) -> bool:
    """True iff ``url`` passes the **Teams** SSRF allowlist."""
    return _host_allowed(url, allowed_webhook_hosts())


def assert_allowed_webhook(url: str) -> None:
    """Raise ``InvalidWebhookError`` unless ``url`` passes the Teams allowlist."""
    if not is_allowed_webhook(url):
        raise InvalidWebhookError(
            "webhook must be an https URL on an allowed host",
            detail={"allowed_hosts": list(allowed_webhook_hosts())},
        )


def assert_allowed_slack_webhook(url: str) -> None:
    """Raise ``InvalidWebhookError`` unless ``url`` passes the Slack allowlist (#633)."""
    if not _host_allowed(url, allowed_slack_hosts()):
        raise InvalidWebhookError(
            "Slack webhook must be an https URL on an allowed host",
            detail={"allowed_hosts": list(allowed_slack_hosts())},
        )


def parse_recipients(raw: str) -> list[str]:
    """Split a comma-separated recipient string into stripped, non-empty addresses."""
    return [part.strip() for part in raw.split(",") if part.strip()]


def assert_valid_recipients(raw: str) -> None:
    """Raise ``InvalidRecipientsError`` unless every comma-part looks like an email.

    A deliberately light check (one ``@`` with non-empty local + domain) — the SMTP
    server is the real validator; this just catches obvious typos before storing."""
    parts = parse_recipients(raw)
    if not parts:
        raise InvalidRecipientsError("at least one recipient is required")
    for addr in parts:
        local, sep, domain = addr.partition("@")
        if not sep or not local or "." not in domain:
            raise InvalidRecipientsError(
                "each recipient must be a valid email address", detail={"invalid": addr}
            )


def get_config(session: Session, suite_id: uuid.UUID) -> SuiteNotification | None:
    """The suite's notification config, or None if it has never been saved."""
    return session.scalars(
        select(SuiteNotification).where(SuiteNotification.suite_id == suite_id)
    ).first()


def _apply_secret_webhook(
    value: str | None,
    current_ref: str | None,
    *,
    ref_prefix: str,
    config_id: uuid.UUID,
    secret_store: SecretStore,
) -> tuple[str | None, str | None]:
    """Apply a tri-state webhook change to a secret-backed ref column.

    Returns ``(new_ref, cleared_ref)``. ``value`` is tri-state: ``None`` leaves the
    ref unchanged; ``""`` clears it (``new_ref=None``, ``cleared_ref`` = the old ref
    to soft-delete AFTER commit, #372); a non-empty value is written through the
    SecretStore under a UNIQUE fresh ref (avoids Key Vault soft-deleted-name reuse)
    or, on rotation, the live ref (a new version). The ``set`` happens here, before
    the caller's commit — matching the connection-credential write-through.
    """
    if value is None:
        return current_ref, None
    if value == "":
        return None, current_ref
    ref = current_ref or f"{ref_prefix}-{config_id}-{uuid.uuid4().hex[:12]}"
    secret_store.set(ref, value)
    return ref, None


def upsert_config(
    session: Session,
    *,
    suite_id: uuid.UUID,
    enabled: bool,
    alert_on: str,
    webhook: str | None,
    slack_webhook: str | None = None,
    email_recipients: str | None = None,
    secret_store: SecretStore,
) -> SuiteNotification:
    """Create or update a suite's notification config.

    Each channel override is **tri-state**: ``None`` leaves it unchanged, ``""``
    clears it (fall back to the workspace-level config), and a non-empty value sets
    it. ``webhook`` (Teams) and ``slack_webhook`` are token-bearing → written through
    the SecretStore under a per-row ref (#633); ``email_recipients`` is a plain
    comma-separated string stored inline (addresses aren't secrets).
    """
    if alert_on not in ALERT_ON_POLICIES:
        raise InvalidAlertPolicyError(
            "invalid alert policy",
            detail={"alert_on": alert_on, "allowed": list(ALERT_ON_POLICIES)},
        )
    # Validate every supplied value up front (before touching the DB / SecretStore).
    if webhook:
        assert_allowed_webhook(webhook)
    if slack_webhook:
        assert_allowed_slack_webhook(slack_webhook)
    if email_recipients:
        assert_valid_recipients(email_recipients)

    config = get_config(session, suite_id)
    if config is None:
        try:
            # SAVEPOINT so a concurrent first-write losing the unique race
            # (uq_suite_notifications_suite_id) rolls back just this insert, not the
            # whole transaction. flush() assigns the id for the secret refs below and
            # surfaces the conflict here.
            with session.begin_nested():
                config = SuiteNotification(suite_id=suite_id, enabled=enabled, alert_on=alert_on)
                session.add(config)
                session.flush()
        except IntegrityError:
            # A concurrent request won the insert — update its row instead of
            # 500-ing on the unique violation (#384).
            config = get_config(session, suite_id)
            if config is None:  # pragma: no cover — the winner's row must exist post-rollback
                raise
            config.enabled = enabled
            config.alert_on = alert_on
    else:
        config.enabled = enabled
        config.alert_on = alert_on

    # Teams + Slack webhooks — secret-backed, tri-state; cleared refs are soft-deleted
    # after commit (#372) so a rolled-back commit can't orphan a live ref.
    config.webhook_secret_ref, cleared_teams = _apply_secret_webhook(
        webhook,
        config.webhook_secret_ref,
        ref_prefix="suite-notif",
        config_id=config.id,
        secret_store=secret_store,
    )
    config.slack_webhook_secret_ref, cleared_slack = _apply_secret_webhook(
        slack_webhook,
        config.slack_webhook_secret_ref,
        ref_prefix="suite-notif-slack",
        config_id=config.id,
        secret_store=secret_store,
    )
    # Email recipients — inline, tri-state ("" clears to NULL → workspace fallback).
    if email_recipients is not None:
        config.email_recipients = email_recipients or None

    session.commit()
    session.refresh(config)
    # Post-commit, fail-soft: remove any now-orphaned webhook secrets (#372).
    for cleared in (cleared_teams, cleared_slack):
        if cleared:
            secret_store.delete(cleared)
    log.info("suite_notification_saved", suite_id=str(suite_id), enabled=enabled, alert_on=alert_on)
    return config


def delete_config(session: Session, suite_id: uuid.UUID, *, secret_store: SecretStore) -> bool:
    """Delete a suite's config (revert to defaults). Returns whether a row existed."""
    config = get_config(session, suite_id)
    if config is None:
        return False
    # Capture both webhook refs before delete so we can soft-delete them after commit.
    orphaned_refs = [config.webhook_secret_ref, config.slack_webhook_secret_ref]
    session.delete(config)
    session.commit()
    # Best-effort remove the orphaned per-suite webhook secrets (#372), fail-soft.
    for ref in orphaned_refs:
        if ref:
            secret_store.delete(ref)
    log.info("suite_notification_deleted", suite_id=str(suite_id))
    return True


def _resolve_secret_webhook(
    ref: str | None,
    workspace_secret_name: str | None,
    *,
    secret_store: SecretStore,
    channel: str,
) -> str | None:
    """The webhook URL to deliver to: the per-suite ref, else the workspace secret.

    Returns ``None`` when neither resolves (delivery is then skipped). A missing
    secret is logged (by ref/name, never the value) and falls through.
    """
    if ref:
        try:
            return secret_store.get(ref)
        except SecretNotFoundError:
            log.warning("suite_webhook_unresolved", channel=channel, secret_ref=ref)
    if workspace_secret_name:
        try:
            return secret_store.get(workspace_secret_name)
        except SecretNotFoundError:
            log.warning(
                "workspace_webhook_unresolved", channel=channel, secret_name=workspace_secret_name
            )
    return None


def resolve_webhook(
    config: SuiteNotification | None,
    *,
    secret_store: SecretStore,
    workspace_secret_name: str | None,
) -> str | None:
    """The **Teams** webhook to deliver to: the per-suite one, else the workspace one."""
    ref = config.webhook_secret_ref if config is not None else None
    return _resolve_secret_webhook(
        ref, workspace_secret_name, secret_store=secret_store, channel="teams"
    )


def resolve_slack_webhook(
    config: SuiteNotification | None,
    *,
    secret_store: SecretStore,
    workspace_secret_name: str | None,
) -> str | None:
    """The **Slack** webhook to deliver to: the per-suite one, else the workspace one (#633)."""
    ref = config.slack_webhook_secret_ref if config is not None else None
    return _resolve_secret_webhook(
        ref, workspace_secret_name, secret_store=secret_store, channel="slack"
    )


def resolve_email_recipients(
    config: SuiteNotification | None,
    *,
    workspace_recipients: tuple[str, ...],
) -> tuple[str, ...]:
    """The **email** recipients to deliver to: the per-suite list, else the workspace
    ``EMAIL_TO`` (#633). Empty tuple when neither is set (delivery is then skipped)."""
    if config is not None and config.email_recipients:
        return tuple(parse_recipients(config.email_recipients))
    return workspace_recipients
