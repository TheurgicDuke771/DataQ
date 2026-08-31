"""Reusable notification channels (#1514) — a destination, defined once, referenced
from many suites. Channel CRUD is Admin-gated (a webhook URL is a token-bearing
credential, same rationale as a `Connection`'s secret); linking/unlinking a suite
follows the suite's own `view`/`edit` ladder. Routing policy (`alert_on`, enabled,
auto-resolve) stays on `SuiteNotification` — a channel is only ever a destination.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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


class ChannelFieldMismatchError(DataQError):
    """Raised when a destination field is supplied for the wrong channel type —
    e.g. a ``webhook`` on an ``email`` channel. Rejected outright rather than
    silently ignored: a caller who names the wrong field for the type should get
    a 422 naming the mismatch, not a 201/200 that quietly did nothing with it.
    """

    status_code = 422
    code = "channel_field_mismatch"


def _validate_type(type_: str) -> None:
    if type_ not in NOTIFICATION_CHANNEL_TYPES:
        raise ChannelTypeInvalidError(
            "invalid channel type",
            detail={"type": type_, "allowed": list(NOTIFICATION_CHANNEL_TYPES)},
        )


def _validate_destination(
    type_: str,
    *,
    webhook: str | None,
    email_recipients: str | None,
    webhook_url: str | None = None,
    payload_template: dict[str, Any] | None = None,
    auth_header_name: str | None = None,
    auth_header_value: str | None = None,
) -> None:
    """Same validators the per-suite fields already use — one allowlist, one rule
    set — plus a type/field match check: ``None`` means "not supplied" and is
    always fine, but a caller who explicitly names a field the type doesn't use
    (``webhook`` on ``email``, ``email_recipients`` on ``teams``/``slack``) gets
    a 422, not a silently-dropped write.
    """
    field_types: dict[str, tuple[object | None, tuple[str, ...]]] = {
        "webhook": (webhook, ("teams", "slack")),
        "email_recipients": (email_recipients, ("email",)),
        "webhook_url": (webhook_url, ("webhook",)),
        "payload_template": (payload_template, ("webhook",)),
        "auth_header_name": (auth_header_name, ("webhook",)),
        "auth_header_value": (auth_header_value, ("webhook",)),
    }
    for field, (value, allowed_types) in field_types.items():
        if value is not None and type_ not in allowed_types:
            raise ChannelFieldMismatchError(
                f"{field!r} does not apply to a {type_!r} channel", detail={"type": type_}
            )
    if type_ == "teams" and webhook:
        notification_service.assert_allowed_webhook(webhook)
    elif type_ == "slack" and webhook:
        notification_service.assert_allowed_slack_webhook(webhook)
    elif type_ == "email" and email_recipients:
        notification_service.assert_valid_recipients(email_recipients)
    elif type_ == "webhook" and webhook_url:
        notification_service.assert_safe_generic_webhook_url(webhook_url)
    if type_ == "webhook" and payload_template is not None:
        notification_service.assert_valid_payload_template(payload_template)
    if type_ == "webhook" and auth_header_name:
        notification_service.assert_valid_auth_header_name(auth_header_name)


@dataclass(frozen=True)
class WebhookDestination:
    """One resolved generic-webhook send target — everything `WebhookPublisher`
    needs to build and sign a request, already pulled out of the SecretStore.
    """

    url: str
    hmac_secret: str
    payload_template: dict[str, Any] | None
    auth_header_name: str | None
    auth_header_value: str | None


def _mint_hmac_secret(
    channel: NotificationChannel, secret_store: SecretStore, *, previous_ref: str | None = None
) -> str:
    """Generate a fresh HMAC signing key and store it — rotating in place when
    ``previous_ref`` names an existing ref (the old value there is replaced,
    then explicitly purged), the same tri-state rotation contract every other
    webhook secret on this table uses via `apply_secret_webhook`.
    """
    hmac_secret = secrets.token_urlsafe(32)
    channel.hmac_secret_ref, cleared = notification_service.apply_secret_webhook(
        hmac_secret,
        previous_ref,
        ref_prefix="channel-hmac",
        config_id=channel.id,
        secret_store=secret_store,
    )
    if cleared:
        secret_store.delete(cleared)
    return hmac_secret


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
    webhook_url: str | None = None,
    payload_template: dict[str, Any] | None = None,
    auth_header_name: str | None = None,
    auth_header_value: str | None = None,
    secret_store: SecretStore,
    actor_id: uuid.UUID | None = None,
) -> tuple[NotificationChannel, str | None]:
    """Returns ``(channel, hmac_secret)`` — the second element is the freshly
    minted HMAC signing key in plaintext, populated only when a ``webhook``-type
    channel's URL was set on this call (DataQ generates it; it is never taken
    from the caller and never retrievable again after this response).
    """
    _validate_type(type)
    _validate_destination(
        type,
        webhook=webhook,
        email_recipients=email_recipients,
        webhook_url=webhook_url,
        payload_template=payload_template,
        auth_header_name=auth_header_name,
        auth_header_value=auth_header_value,
    )

    channel = NotificationChannel(name=name, type=type, created_by=actor_id)
    session.add(channel)
    session.flush()  # mint channel.id before it's used in the secret ref below

    if type in ("teams", "slack") and webhook:
        # A create has no prior ref to clear, so the tri-state helper's second
        # return value is always None here — only the minted ref is kept.
        channel.webhook_secret_ref = notification_service.apply_secret_webhook(
            webhook, None, ref_prefix="channel", config_id=channel.id, secret_store=secret_store
        )[0]
    if type == "email" and email_recipients:
        channel.email_recipients = email_recipients
    hmac_secret: str | None = None
    if type == "webhook":
        if webhook_url:
            channel.webhook_url = webhook_url
            hmac_secret = _mint_hmac_secret(channel, secret_store)
        if payload_template is not None:
            channel.payload_template = payload_template
        if auth_header_name:
            channel.auth_header_name = auth_header_name
        if auth_header_value:
            channel.auth_header_secret_ref = notification_service.apply_secret_webhook(
                auth_header_value,
                None,
                ref_prefix="channel-auth",
                config_id=channel.id,
                secret_store=secret_store,
            )[0]

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
    return channel, hmac_secret


def update_channel(
    session: Session,
    channel_id: uuid.UUID,
    *,
    name: str | None = None,
    webhook: str | None = None,
    email_recipients: str | None = None,
    webhook_url: str | None = None,
    payload_template: dict[str, Any] | None = None,
    clear_payload_template: bool = False,
    auth_header_name: str | None = None,
    auth_header_value: str | None = None,
    regenerate_hmac_secret: bool = False,
    secret_store: SecretStore,
    actor_id: uuid.UUID | None = None,
) -> tuple[NotificationChannel, str | None]:
    """``webhook``/``email_recipients``/``webhook_url``/``auth_header_value``
    are tri-state, same convention as `notification_service.upsert_config`:
    ``None`` = unchanged, ``""`` = clear, a value = set/rotate. Rotating a
    channel referenced by N suites is the point — this is the one place that
    value changes. ``payload_template`` can't use the same "" = clear
    convention (an empty object is a legitimate template value, distinct from
    "no template"), so clearing it is the explicit ``clear_payload_template``
    flag instead. ``auth_header_name`` is plain text, not tri-state — passing
    ``""`` clears it (there's no rotate-in-place concept for a header name).

    Returns ``(channel, hmac_secret)`` — the second element is the newly minted
    plaintext HMAC key, populated when ``regenerate_hmac_secret`` is set, or
    when a ``webhook``-type channel's URL is being set for the first time
    (mints the signing key it never had). ``None`` otherwise — the existing key
    is never re-shown.
    """
    channel = get_channel(session, channel_id)
    _validate_destination(
        channel.type,
        webhook=webhook,
        email_recipients=email_recipients,
        webhook_url=webhook_url,
        payload_template=payload_template,
        auth_header_name=auth_header_name,
        auth_header_value=auth_header_value,
    )
    audit_before = audit_service.snapshot("notification_channel", channel)

    if name is not None:
        channel.name = name

    cleared_secret: str | None = None
    if channel.type in ("teams", "slack") and webhook is not None:
        channel.webhook_secret_ref, cleared_secret = notification_service.apply_secret_webhook(
            webhook,
            channel.webhook_secret_ref,
            ref_prefix="channel",
            config_id=channel.id,
            secret_store=secret_store,
        )
    if channel.type == "email" and email_recipients is not None:
        channel.email_recipients = email_recipients or None

    hmac_secret: str | None = None
    cleared_auth_header: str | None = None
    if channel.type == "webhook":
        if webhook_url is not None:
            channel.webhook_url = webhook_url or None
        # Two distinct triggers, kept named rather than collapsed into one
        # opaque flag: an explicit rotation request, or the implicit
        # first-ever mint when a URL is set on a channel with no key yet.
        first_time_mint = channel.webhook_url is not None and channel.hmac_secret_ref is None
        if regenerate_hmac_secret or first_time_mint:
            hmac_secret = _mint_hmac_secret(
                channel, secret_store, previous_ref=channel.hmac_secret_ref
            )
        if payload_template is not None:
            channel.payload_template = payload_template
        elif clear_payload_template:
            channel.payload_template = None
        if auth_header_name is not None:
            channel.auth_header_name = auth_header_name or None
        if auth_header_value is not None:
            channel.auth_header_secret_ref, cleared_auth_header = (
                notification_service.apply_secret_webhook(
                    auth_header_value,
                    channel.auth_header_secret_ref,
                    ref_prefix="channel-auth",
                    config_id=channel.id,
                    secret_store=secret_store,
                )
            )

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
    if cleared_auth_header:
        secret_store.delete(cleared_auth_header)
    return channel, hmac_secret


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
    hmac_ref = channel.hmac_secret_ref
    auth_header_ref = channel.auth_header_secret_ref
    audit_before = audit_service.snapshot("notification_channel", channel)
    try:
        # SAVEPOINT: a link committed between the check above and this delete trips
        # the ON DELETE RESTRICT FK — caught here and reported as the same clean 409
        # the pre-check gives, not an unhandled IntegrityError (500).
        with session.begin_nested():
            session.delete(channel)
            session.flush()
    except IntegrityError:
        linked = _linked_suites_detail(session, channel_id) or {"total": 0, "suites": []}
        raise ChannelInUseError(
            f"{linked['total']} suite(s) still reference this channel — unlink them first",
            detail=linked,
        ) from None
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
    if hmac_ref:
        secret_store.delete(hmac_ref)
    if auth_header_ref:
        secret_store.delete(auth_header_ref)


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


def resolve_webhook_channels(
    session: Session, suite_id: uuid.UUID, *, secret_store: SecretStore
) -> list[WebhookDestination]:
    """Every distinct :class:`WebhookDestination` for the suite's linked
    ``webhook`` channels — a channel missing the URL or the HMAC signing key
    (never configured, or the secret has gone missing) is skipped and logged,
    same fail-soft posture as :func:`resolve_channel_webhooks`, which this also
    matches by deduping on URL (two channels that happen to point at the same
    destination deliver once, not twice). The signing key never leaves the
    process except as an HMAC digest.

    An unresolvable AUTH HEADER secret is a softer failure than a missing HMAC
    key: the header is an extra, optional layer some receivers want beside the
    signature, not the thing securing the request, so the destination still
    delivers — just without that header — rather than being dropped entirely.
    """
    destinations: list[WebhookDestination] = []
    seen_urls: set[str] = set()
    for channel in list_channels_for_suite(session, suite_id):
        if channel.type != "webhook" or not channel.webhook_url or not channel.hmac_secret_ref:
            continue
        if channel.webhook_url in seen_urls:
            continue
        try:
            hmac_secret = secret_store.get(channel.hmac_secret_ref)
        except SecretNotFoundError:
            log.warning("notification_channel_hmac_unresolved", channel_id=str(channel.id))
            continue
        auth_header_value: str | None = None
        if channel.auth_header_name and channel.auth_header_secret_ref:
            try:
                auth_header_value = secret_store.get(channel.auth_header_secret_ref)
            except SecretNotFoundError:
                log.warning(
                    "notification_channel_auth_header_unresolved", channel_id=str(channel.id)
                )
        seen_urls.add(channel.webhook_url)
        destinations.append(
            WebhookDestination(
                url=channel.webhook_url,
                hmac_secret=hmac_secret,
                payload_template=channel.payload_template,
                auth_header_name=channel.auth_header_name if auth_header_value else None,
                auth_header_value=auth_header_value,
            )
        )
    return destinations


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
    try:
        # SAVEPOINT so a concurrent first-linker winning the composite-PK race rolls
        # back just this insert, not the whole transaction — same shape as
        # notification_service.upsert_config's fix for the identical race (#384).
        with session.begin_nested():
            session.add(link)
            session.flush()
    except IntegrityError:
        # Someone else linked this exact pair between our check and our insert —
        # the no-op state the caller asked for either way.
        return
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
