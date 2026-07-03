"""Schedule CRUD endpoints — manage cron-driven suite run schedules (A7).

Thin HTTP layer over `schedule_service`: a schedule fires a suite run on a cron
cadence (`cron` + `timezone` → `suite_id`). All validation (cron / timezone /
suite-permission) and `next_run_at` bookkeeping live in the service.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import ConfigDict, Field
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel
from backend.app.core.auth import get_current_user
from backend.app.db.models import Schedule, User
from backend.app.db.session import get_db
from backend.app.services import schedule_service as svc

router = APIRouter(tags=["schedules"])


class ScheduleCreate(ApiModel):
    suite_id: uuid.UUID
    cron: str = Field(min_length=1, max_length=128)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    enabled: bool = True


class ScheduleUpdate(ApiModel):
    """Partial update — only the supplied fields change. `next_run_at` is
    recomputed by the service when the cadence changes or a paused schedule is
    re-enabled."""

    cron: str | None = Field(default=None, min_length=1, max_length=128)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    enabled: bool | None = None


class ScheduleRead(ApiModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    suite_id: uuid.UUID
    cron: str
    timezone: str
    enabled: bool
    next_run_at: datetime
    last_run_at: datetime | None


@router.post(
    "/schedules",
    response_model=ScheduleRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a suite run schedule",
)
def create_schedule(
    payload: ScheduleCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Schedule:
    return svc.create_schedule(
        db,
        suite_id=payload.suite_id,
        cron_expr=payload.cron,
        timezone=payload.timezone,
        enabled=payload.enabled,
        user_id=current_user.id,
    )


@router.get(
    "/schedules",
    response_model=list[ScheduleRead],
    summary="List schedules on accessible suites",
)
def list_schedules(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    suite_id: uuid.UUID | None = None,
    enabled: bool | None = None,
) -> list[Schedule]:
    return svc.list_schedules(db, user_id=current_user.id, suite_id=suite_id, enabled=enabled)


@router.get(
    "/schedules/{schedule_id}",
    response_model=ScheduleRead,
    summary="Get a schedule",
)
def get_schedule(
    schedule_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Schedule:
    return svc.get_schedule(db, schedule_id, user_id=current_user.id)


@router.patch(
    "/schedules/{schedule_id}",
    response_model=ScheduleRead,
    summary="Update a schedule (cron / timezone / enabled)",
)
def update_schedule(
    schedule_id: uuid.UUID,
    payload: ScheduleUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Schedule:
    return svc.update_schedule(
        db,
        schedule_id,
        user_id=current_user.id,
        cron_expr=payload.cron,
        timezone=payload.timezone,
        enabled=payload.enabled,
    )


@router.delete(
    "/schedules/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a schedule",
)
def delete_schedule(
    schedule_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    svc.delete_schedule(db, schedule_id, user_id=current_user.id)
