"""Reusable notification channels (#1514) — Admin-gated CRUD, plus per-suite
link/unlink on the suite's own view/edit ladder. Mirrors the `/connections`
split: reads are open to any authenticated user (a suite editor picks a channel
from a list), mutations are `AdminUser`-only (a webhook URL is a credential).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, status
from pydantic import Field
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel, ApiRequestModel
from backend.app.core.auth import AdminUser, get_current_user
from backend.app.core.roles import is_workspace_admin
from backend.app.core.secrets import SecretStore, get_secret_store
from backend.app.db.models import NotificationChannel, User
from backend.app.db.session import get_db
from backend.app.services import channel_service as svc
from backend.app.services.suite_authz import require_permission

router = APIRouter(tags=["notification-channels"])


class ChannelRead(ApiModel):
    """A channel's shape as any authenticated caller may see it. The Teams/Slack
    webhook and the generic webhook's HMAC signing key are credentials and
    never returned, only whether one is set — `webhook_url` is the exception:
    for a generic webhook it is the destination, not the credential (the HMAC
    signature is), so it is safe to echo back for admin visibility.

    `payload_template` is a partial exception: it isn't itself a SecretStore-
    backed credential column, but an admin authoring one (e.g. a PagerDuty
    Events-API body) commonly has nowhere else to put that receiver's static
    routing/integration key than as a literal in the template JSON — which
    functions as a credential even though the field's storage doesn't treat
    it as one (#1663 review). It's therefore only ever included for an
    Admin caller; every other authenticated user gets `has_payload_template`
    instead, the same presence-only shape already used for genuine secrets.
    """

    id: uuid.UUID
    name: str
    type: str
    has_webhook: bool
    email_recipients: str | None
    webhook_url: str | None = None
    has_hmac_secret: bool = False
    #: Populated ONLY by create/rotate, in the response of that one call — the
    #: plaintext HMAC signing key, shown exactly once. Never set by a read.
    hmac_secret: str | None = None
    #: Admin-only — see the class docstring. `None` for a non-admin caller
    #: regardless of whether a template is actually set; use
    #: `has_payload_template` to tell "unset" from "set but not shown".
    payload_template: dict[str, Any] | None = None
    has_payload_template: bool = False
    auth_header_name: str | None = None
    has_auth_header: bool = False

    @classmethod
    def from_model(
        cls,
        channel: NotificationChannel,
        *,
        hmac_secret: str | None = None,
        include_payload_template: bool = True,
    ) -> ChannelRead:
        return cls(
            id=channel.id,
            name=channel.name,
            type=channel.type,
            has_webhook=channel.webhook_secret_ref is not None,
            email_recipients=channel.email_recipients,
            webhook_url=channel.webhook_url,
            has_hmac_secret=channel.hmac_secret_ref is not None,
            hmac_secret=hmac_secret,
            payload_template=channel.payload_template if include_payload_template else None,
            has_payload_template=channel.payload_template is not None,
            auth_header_name=channel.auth_header_name,
            has_auth_header=channel.auth_header_secret_ref is not None,
        )


class ChannelCreate(ApiRequestModel):
    name: str = Field(min_length=1, max_length=128)
    type: Literal["teams", "slack", "email", "webhook"]
    webhook: str | None = Field(default=None, description="Teams/Slack webhook URL; write-only")
    email_recipients: str | None = Field(default=None, description="Comma-separated addresses")
    webhook_url: str | None = Field(
        default=None, description="Generic webhook destination URL (https, non-internal)"
    )
    payload_template: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Reshapes the generic webhook payload for a specific receiver "
            "(PagerDuty/Opsgenie/ServiceNow/Jira). {{field.path}} placeholders "
            "resolve against the generic payload by key lookup only. WARNING: this "
            "is stored as plain JSON, not a SecretStore-backed credential — never "
            "paste a real secret in as a literal value (a routing/integration key, "
            "an API token). Put any credential in auth_header_value instead, which "
            "is write-only and encrypted at rest; readable only by workspace admins."
        ),
    )
    auth_header_name: str | None = Field(
        default=None, description="An extra header some receivers need beside the HMAC signature"
    )
    auth_header_value: str | None = Field(default=None, description="The header value; write-only")


class ChannelUpdate(ApiRequestModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    # Tri-state, same convention as SuiteNotificationUpdate: omit = unchanged, "" = clear,
    # value = set/rotate.
    webhook: str | None = None
    email_recipients: str | None = None
    webhook_url: str | None = None
    regenerate_hmac_secret: bool = Field(
        default=False, description="Mint a new HMAC signing key, invalidating the old one"
    )
    payload_template: dict[str, Any] | None = Field(
        default=None, description="Set/replace the payload template"
    )
    clear_payload_template: bool = Field(
        default=False,
        description="Remove the template (an empty object is a legitimate template, so clearing "
        "needs its own flag rather than overloading an empty payload_template)",
    )
    auth_header_name: str | None = Field(
        default=None, description="Set the auth header name; empty string clears it"
    )
    auth_header_value: str | None = None


@router.get("/notification-channels", response_model=list[ChannelRead], summary="List channels")
def list_channels(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[ChannelRead]:
    include_template = is_workspace_admin(current_user)
    return [
        ChannelRead.from_model(c, include_payload_template=include_template)
        for c in svc.list_channels(db)
    ]


@router.get(
    "/notification-channels/{channel_id}", response_model=ChannelRead, summary="Get a channel"
)
def get_channel(
    channel_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ChannelRead:
    return ChannelRead.from_model(
        svc.get_channel(db, channel_id),
        include_payload_template=is_workspace_admin(current_user),
    )


@router.post(
    "/notification-channels",
    response_model=ChannelRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a channel",
)
def create_channel(
    payload: ChannelCreate,
    current_user: AdminUser,
    db: Annotated[Session, Depends(get_db)],
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
) -> ChannelRead:
    channel, hmac_secret = svc.create_channel(
        db,
        name=payload.name,
        type=payload.type,
        webhook=payload.webhook,
        email_recipients=payload.email_recipients,
        webhook_url=payload.webhook_url,
        payload_template=payload.payload_template,
        auth_header_name=payload.auth_header_name,
        auth_header_value=payload.auth_header_value,
        secret_store=secret_store,
        actor_id=current_user.id,
    )
    return ChannelRead.from_model(channel, hmac_secret=hmac_secret)


@router.patch(
    "/notification-channels/{channel_id}", response_model=ChannelRead, summary="Update a channel"
)
def update_channel(
    channel_id: uuid.UUID,
    payload: ChannelUpdate,
    current_user: AdminUser,
    db: Annotated[Session, Depends(get_db)],
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
) -> ChannelRead:
    channel, hmac_secret = svc.update_channel(
        db,
        channel_id,
        name=payload.name,
        webhook=payload.webhook,
        email_recipients=payload.email_recipients,
        webhook_url=payload.webhook_url,
        payload_template=payload.payload_template,
        clear_payload_template=payload.clear_payload_template,
        auth_header_name=payload.auth_header_name,
        auth_header_value=payload.auth_header_value,
        regenerate_hmac_secret=payload.regenerate_hmac_secret,
        secret_store=secret_store,
        actor_id=current_user.id,
    )
    return ChannelRead.from_model(channel, hmac_secret=hmac_secret)


@router.delete(
    "/notification-channels/{channel_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a channel (refused while any suite still references it)",
)
def delete_channel(
    channel_id: uuid.UUID,
    current_user: AdminUser,
    db: Annotated[Session, Depends(get_db)],
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
) -> None:
    svc.delete_channel(db, channel_id, secret_store=secret_store, actor_id=current_user.id)


@router.get(
    "/suites/{suite_id}/notification-channels",
    response_model=list[ChannelRead],
    summary="List a suite's linked channels",
)
def list_suite_channels(
    suite_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[ChannelRead]:
    require_permission(db, suite_id, current_user.id, minimum="view")
    include_template = is_workspace_admin(current_user)
    return [
        ChannelRead.from_model(c, include_payload_template=include_template)
        for c in svc.list_channels_for_suite(db, suite_id)
    ]


@router.put(
    "/suites/{suite_id}/notification-channels/{channel_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Link a channel to a suite (idempotent)",
)
def link_suite_channel(
    suite_id: uuid.UUID,
    channel_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    require_permission(db, suite_id, current_user.id, minimum="edit")
    svc.link_suite(db, suite_id, channel_id, actor_id=current_user.id)


@router.delete(
    "/suites/{suite_id}/notification-channels/{channel_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Unlink a channel from a suite",
)
def unlink_suite_channel(
    suite_id: uuid.UUID,
    channel_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    require_permission(db, suite_id, current_user.id, minimum="edit")
    svc.unlink_suite(db, suite_id, channel_id, actor_id=current_user.id)
