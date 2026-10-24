"""The caller's own in-app notification feed (#1687): list, unread count, read-acks.

Every route is scoped to the signed-in user's rows and nothing else — there is no
cross-user read, no admin override, and another user's entry is a 404, never a 403.
Workspace Viewers may read and acknowledge their own feed: an ack is per-user state,
not a suite mutation (ADR 0033 clamps only suite edits).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from backend.app.api.v1._base import TOTAL_COUNT_HEADER, ApiModel, total_count_responses
from backend.app.core.auth import get_current_user
from backend.app.db.models import Notification, User
from backend.app.db.session import get_db
from backend.app.services import notification_feed_service as svc

router = APIRouter(tags=["notification-feed"])


class NotificationRead(ApiModel):
    """One entry of the caller's feed. `deep_link` is the run page while the run still
    exists and null once it is gone (the entry outlives the run); `incident_link` is the
    incident's own view while the incident exists.
    """

    id: uuid.UUID
    suite_id: uuid.UUID
    run_id: uuid.UUID | None
    incident_id: uuid.UUID | None
    # pass | warn | fail | critical | error — the run's worst tier, `error` = failed to execute.
    severity: str
    title: str
    body: str
    deep_link: str | None
    incident_link: str | None
    created_at: datetime
    read_at: datetime | None


class UnreadCountRead(ApiModel):
    unread: int


class ReadAllResult(ApiModel):
    # How many entries this call flipped from unread to read (0 when nothing was unread).
    marked: int


def _to_read(row: Notification, asset_id: uuid.UUID | None) -> NotificationRead:
    return NotificationRead(
        id=row.id,
        suite_id=row.suite_id,
        run_id=row.run_id,
        incident_id=row.incident_id,
        severity=row.severity,
        title=row.title,
        body=row.body,
        deep_link=row.deep_link if row.run_id is not None else None,
        incident_link=(
            f"/assets/{asset_id}?incident={row.incident_id}"
            if row.incident_id is not None and asset_id is not None
            else None
        ),
        created_at=row.created_at,
        read_at=row.read_at,
    )


@router.get(
    "/notifications",
    response_model=list[NotificationRead],
    summary="List my notifications (newest first)",
    responses=total_count_responses(
        "Total entries in the caller's own feed matching `unread_only` — the population this "
        "page's limit/offset slice into; a page shorter than `limit` does not by itself "
        "prove there are no more."
    ),
)
def list_notifications(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    response: Response,
    unread_only: bool = Query(default=False, description="Only entries not yet marked read"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[NotificationRead]:
    response.headers[TOTAL_COUNT_HEADER] = str(
        svc.count_feed(db, user_id=current_user.id, unread_only=unread_only)
    )
    rows = svc.list_feed(
        db, user_id=current_user.id, unread_only=unread_only, limit=limit, offset=offset
    )
    return [_to_read(row, asset_id) for row, asset_id in rows]


@router.get(
    "/notifications/unread_count",
    response_model=UnreadCountRead,
    summary="How many of my notifications are unread",
)
def get_unread_count(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> UnreadCountRead:
    return UnreadCountRead(unread=svc.unread_count(db, user_id=current_user.id))


@router.post(
    "/notifications/read_all",
    response_model=ReadAllResult,
    summary="Mark all my notifications read",
)
def read_all_notifications(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ReadAllResult:
    return ReadAllResult(marked=svc.mark_all_read(db, user_id=current_user.id))


@router.post(
    "/notifications/{notification_id}/read",
    response_model=NotificationRead,
    summary="Mark one of my notifications read",
)
def read_notification(
    notification_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> NotificationRead:
    row, asset_id = svc.mark_read(db, user_id=current_user.id, notification_id=notification_id)
    return _to_read(row, asset_id)
