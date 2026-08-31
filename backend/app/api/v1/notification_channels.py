"""Reusable notification channels (#1514) — Admin-gated CRUD, plus per-suite
link/unlink on the suite's own view/edit ladder. Mirrors the `/connections`
split: reads are open to any authenticated user (a suite editor picks a channel
from a list), mutations are `AdminUser`-only (a webhook URL is a credential).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, status
from pydantic import Field
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel, ApiRequestModel
from backend.app.core.auth import AdminUser, get_current_user
from backend.app.core.secrets import SecretStore, get_secret_store
from backend.app.db.models import NotificationChannel, User
from backend.app.db.session import get_db
from backend.app.services import channel_service as svc
from backend.app.services.suite_authz import require_permission

router = APIRouter(tags=["notification-channels"])


class ChannelRead(ApiModel):
    """A channel's shape as any authenticated caller may see it — the webhook URL
    is a secret and never returned, only whether one is set.
    """

    id: uuid.UUID
    name: str
    type: str
    has_webhook: bool
    email_recipients: str | None

    @classmethod
    def from_model(cls, channel: NotificationChannel) -> ChannelRead:
        return cls(
            id=channel.id,
            name=channel.name,
            type=channel.type,
            has_webhook=channel.webhook_secret_ref is not None,
            email_recipients=channel.email_recipients,
        )


class ChannelCreate(ApiRequestModel):
    name: str = Field(min_length=1, max_length=128)
    type: Literal["teams", "slack", "email"]
    webhook: str | None = Field(default=None, description="Teams/Slack webhook URL; write-only")
    email_recipients: str | None = Field(default=None, description="Comma-separated addresses")


class ChannelUpdate(ApiRequestModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    # Tri-state, same convention as SuiteNotificationUpdate: omit = unchanged, "" = clear,
    # value = set/rotate.
    webhook: str | None = None
    email_recipients: str | None = None


@router.get("/notification-channels", response_model=list[ChannelRead], summary="List channels")
def list_channels(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[ChannelRead]:
    return [ChannelRead.from_model(c) for c in svc.list_channels(db)]


@router.get(
    "/notification-channels/{channel_id}", response_model=ChannelRead, summary="Get a channel"
)
def get_channel(
    channel_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ChannelRead:
    return ChannelRead.from_model(svc.get_channel(db, channel_id))


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
    channel = svc.create_channel(
        db,
        name=payload.name,
        type=payload.type,
        webhook=payload.webhook,
        email_recipients=payload.email_recipients,
        secret_store=secret_store,
        actor_id=current_user.id,
    )
    return ChannelRead.from_model(channel)


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
    channel = svc.update_channel(
        db,
        channel_id,
        name=payload.name,
        webhook=payload.webhook,
        email_recipients=payload.email_recipients,
        secret_store=secret_store,
        actor_id=current_user.id,
    )
    return ChannelRead.from_model(channel)


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
    return [ChannelRead.from_model(c) for c in svc.list_channels_for_suite(db, suite_id)]


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
