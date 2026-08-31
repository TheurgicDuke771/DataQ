"""Reusable notification channels (#1514) — a destination, defined once, referenced
from many suites. Channel CRUD is Admin-gated (a webhook URL is a token-bearing
credential, same rationale as a `Connection`'s secret); linking/unlinking a suite
follows the suite's own `view`/`edit` ladder. Routing policy (`alert_on`, enabled,
auto-resolve) stays on `SuiteNotification` — a channel is only ever a destination.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretNotFoundError, SecretStore
from backend.app.db.models import (
    NOTIFICATION_CHANNEL_TYPES,
    NotificationChannel,
    Suite,
    SuiteNotificationChannel,
)
from backend.app.services import audit_service, notification_service

log = get_logger(__name__)


class ChannelNotFoundError(DataQError):
    status_code = 404
    code = "channel_not_found"


class ChannelTypeInvalidError(DataQError):
    status_code = 422
    code = "channel_type_invalid"


class ChannelInUseError(DataQError):
    """Raised when deleting a channel still linked to at least one suite."""

    status_code = 409
    code = "channel_in_use"


def _validate_type(type_: str) -> None:
    if type_ not in NOTIFICATION_CHANNEL_TYPES:
        raise ChannelTypeInvalidError(
            "invalid channel type",
            detail={"type": type_, "allowed": list(NOTIFICATION_CHANNEL_TYPES)},
        )


def _validate_destination(type_: str, *, webhook: str | None, email_recipients: str | None) -> None:
    """Same validators the per-suite fields already use — one allowlist, one rule set."""
    if type_ == "teams" and webhook:
        notification_service.assert_allowed_webhook(webhook)
    elif type_ == "slack" and webhook:
        notification_service.assert_allowed_slack_webhook(webhook)
    elif type_ == "email" and email_recipients:
        notification_service.assert_valid_recipients(email_recipients)


def _apply_secret_webhook(
    value: str | None, current_ref: str | None, *, channel_id: uuid.UUID, secret_store: SecretStore
) -> tuple[str | None, str | None]:
    """Apply a tri-state webhook change to the channel's secret-backed ref — same
    convention as `notification_service`'s per-suite fields: ``None`` = unchanged,
    ``""`` = clear, a value = set/rotate. Returns ``(new_ref, ref_to_delete)``.
    """
    if value is None:
        return current_ref, None
    if value == "":
        return None, current_ref
    ref = current_ref or f"channel-{channel_id}-{uuid.uuid4().hex[:12]}"
    secret_store.set(ref, value)
    return ref, None


def get_channel(session: Session, channel_id: uuid.UUID) -> NotificationChannel:
    channel = session.get(NotificationChannel, channel_id)
    if channel is None:
        raise ChannelNotFoundError("notification channel not found")
    return channel


def list_channels(session: Session) -> list[NotificationChannel]:
    return list(session.scalars(select(NotificationChannel).order_by(NotificationChannel.name)))


def create_channel(
    session: Session,
    *,
    name: str,
    type: str,
    webhook: str | None = None,
    email_recipients: str | None = None,
    secret_store: SecretStore,
    actor_id: uuid.UUID | None = None,
) -> NotificationChannel:
    _validate_type(type)
    _validate_destination(type, webhook=webhook, email_recipients=email_recipients)

    channel = NotificationChannel(name=name, type=type, created_by=actor_id)
    session.add(channel)
    session.flush()  # mint channel.id before it's used in the secret ref below

    if type in ("teams", "slack") and webhook:
        ref = f"channel-{channel.id}-{uuid.uuid4().hex[:12]}"
        secret_store.set(ref, webhook)
        channel.webhook_secret_ref = ref
    if type == "email" and email_recipients:
        channel.email_recipients = email_recipients

    audit_service.record_entity_change(
        session,
        action="notification_channel.create",
        entity_type="notification_channel",
        entity=channel,
        actor=actor_id,
        before=None,
    )
    session.commit()
    session.refresh(channel)
    return channel


def update_channel(
    session: Session,
    channel_id: uuid.UUID,
    *,
    name: str | None = None,
    webhook: str | None = None,
    email_recipients: str | None = None,
    secret_store: SecretStore,
    actor_id: uuid.UUID | None = None,
) -> NotificationChannel:
    """``webhook``/``email_recipients`` are tri-state, same convention as
    `notification_service.upsert_config`: ``None`` = unchanged, ``""`` = clear,
    a value = set/rotate. Rotating a channel referenced by N suites is the point —
    this is the one place that value changes.
    """
    channel = get_channel(session, channel_id)
    _validate_destination(channel.type, webhook=webhook, email_recipients=email_recipients)
    audit_before = audit_service.snapshot("notification_channel", channel)

    if name is not None:
        channel.name = name

    cleared_secret: str | None = None
    if channel.type in ("teams", "slack") and webhook is not None:
        channel.webhook_secret_ref, cleared_secret = _apply_secret_webhook(
            webhook, channel.webhook_secret_ref, channel_id=channel.id, secret_store=secret_store
        )
    if channel.type == "email" and email_recipients is not None:
        channel.email_recipients = email_recipients or None

    audit_service.record_entity_change(
        session,
        action="notification_channel.update",
        entity_type="notification_channel",
        entity=channel,
        actor=actor_id,
        before=audit_before,
    )
    session.commit()
    session.refresh(channel)
    if cleared_secret:
        secret_store.delete(cleared_secret)
    return channel


def _linked_suites_detail(session: Session, channel_id: uuid.UUID) -> dict[str, Any] | None:
    rows = session.execute(
        select(Suite.id, Suite.name)
        .join(SuiteNotificationChannel, SuiteNotificationChannel.suite_id == Suite.id)
        .where(SuiteNotificationChannel.channel_id == channel_id)
        .order_by(Suite.name)
    ).all()
    if not rows:
        return None
    return {
        "total": len(rows),
        "suites": [{"id": str(sid), "name": name} for sid, name in rows],
    }


def delete_channel(
    session: Session,
    channel_id: uuid.UUID,
    *,
    secret_store: SecretStore,
    actor_id: uuid.UUID | None = None,
) -> None:
    channel = get_channel(session, channel_id)
    linked = _linked_suites_detail(session, channel_id)
    if linked:
        raise ChannelInUseError(
            f"{linked['total']} suite(s) still reference this channel — unlink them first",
            detail=linked,
        )
    secret_ref = channel.webhook_secret_ref
    audit_before = audit_service.snapshot("notification_channel", channel)
    session.delete(channel)
    audit_service.record_entity_change(
        session,
        action="notification_channel.delete",
        entity_type="notification_channel",
        entity=None,
        actor=actor_id,
        before=audit_before,
    )
    session.commit()
    if secret_ref:
        secret_store.delete(secret_ref)


def list_channels_for_suite(session: Session, suite_id: uuid.UUID) -> list[NotificationChannel]:
    return list(
        session.scalars(
            select(NotificationChannel)
            .join(
                SuiteNotificationChannel,
                SuiteNotificationChannel.channel_id == NotificationChannel.id,
            )
            .where(SuiteNotificationChannel.suite_id == suite_id)
            .order_by(NotificationChannel.name)
        )
    )


def resolve_channel_webhooks(
    session: Session, suite_id: uuid.UUID, *, channel_type: str, secret_store: SecretStore
) -> list[str]:
    """Every distinct, resolved webhook URL from the suite's linked channels of
    ``channel_type`` — a channel whose secret has gone missing is skipped (logged),
    same fail-soft posture as the per-suite/workspace resolvers.
    """
    urls: list[str] = []
    for channel in list_channels_for_suite(session, suite_id):
        if channel.type != channel_type or not channel.webhook_secret_ref:
            continue
        try:
            url = secret_store.get(channel.webhook_secret_ref)
        except SecretNotFoundError:
            log.warning(
                "notification_channel_webhook_unresolved",
                channel_id=str(channel.id),
                channel_type=channel_type,
            )
            continue
        if url not in urls:
            urls.append(url)
    return urls


def resolve_channel_email_recipients(session: Session, suite_id: uuid.UUID) -> tuple[str, ...]:
    """Every distinct recipient across the suite's linked ``email`` channels."""
    recipients: list[str] = []
    for channel in list_channels_for_suite(session, suite_id):
        if channel.type != "email" or not channel.email_recipients:
            continue
        for addr in notification_service.parse_recipients(channel.email_recipients):
            if addr not in recipients:
                recipients.append(addr)
    return tuple(recipients)


def link_suite(
    session: Session,
    suite_id: uuid.UUID,
    channel_id: uuid.UUID,
    *,
    actor_id: uuid.UUID | None = None,
) -> None:
    """Idempotent: linking an already-linked channel is a no-op, not a conflict —
    the caller asked for a state, not an event.
    """
    get_channel(session, channel_id)  # 404s cleanly if the channel doesn't exist
    existing = session.get(SuiteNotificationChannel, (suite_id, channel_id))
    if existing is not None:
        return
    link = SuiteNotificationChannel(suite_id=suite_id, channel_id=channel_id)
    session.add(link)
    audit_service.record_entity_change(
        session,
        action="suite_notification_channel.link",
        entity_type="suite_notification_channel",
        entity=link,
        actor=actor_id,
        before=None,
    )
    session.commit()


def unlink_suite(
    session: Session,
    suite_id: uuid.UUID,
    channel_id: uuid.UUID,
    *,
    actor_id: uuid.UUID | None = None,
) -> bool:
    link = session.get(SuiteNotificationChannel, (suite_id, channel_id))
    if link is None:
        return False
    audit_before = audit_service.snapshot("suite_notification_channel", link)
    session.delete(link)
    audit_service.record_entity_change(
        session,
        action="suite_notification_channel.unlink",
        entity_type="suite_notification_channel",
        entity=None,
        actor=actor_id,
        before=audit_before,
    )
    session.commit()
    return True
