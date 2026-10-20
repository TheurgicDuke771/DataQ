"""The declared seasonal calendar (ADR 0048 decision 5): read by anyone signed in, so a check
author can attach entries; changed by workspace admins only."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, status
from pydantic import Field
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel, ApiRequestModel
from backend.app.core.auth import AdminUser, get_current_user
from backend.app.db.models import SeasonalPeriod
from backend.app.db.session import get_db
from backend.app.services import seasonal_period_service as svc

router = APIRouter(
    prefix="/admin/seasonal-periods",
    tags=["admin"],
    dependencies=[Depends(get_current_user)],
)

Kind = Literal["recurring", "dated"]


class SeasonalPeriodWrite(ApiRequestModel):
    """A recurring entry sets the four month/day fields (the range may wrap the year end:
    20 December to 5 January; 29 February is taken as 28 February in other years). A dated
    entry sets `start_date` and `end_date`, and optionally a `series`: dated entries sharing
    a series are occurrences of one period across years, which is how a moving sale finds
    its previous occurrence."""

    name: str = Field(max_length=svc.NAME_MAX)
    kind: Kind
    start_month: int | None = None
    start_day: int | None = None
    end_month: int | None = None
    end_day: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    series: str | None = Field(default=None, max_length=svc.SERIES_MAX)
    note: str | None = Field(default=None, max_length=svc.NOTE_MAX)


class SeasonalPeriodPatch(ApiRequestModel):
    """Any field left out keeps its value; the result is validated whole, so a kind change
    must send the new kind's fields."""

    name: str | None = Field(default=None, max_length=svc.NAME_MAX)
    kind: Kind | None = None
    start_month: int | None = None
    start_day: int | None = None
    end_month: int | None = None
    end_day: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    series: str | None = Field(default=None, max_length=svc.SERIES_MAX)
    note: str | None = Field(default=None, max_length=svc.NOTE_MAX)


class SeasonalPeriodRead(ApiModel):
    id: uuid.UUID
    name: str
    kind: Kind
    start_month: int | None
    start_day: int | None
    end_month: int | None
    end_day: int | None
    start_date: date | None
    end_date: date | None
    series: str | None
    note: str | None
    #: Checks whose config names this entry. Deleting is refused while it is above zero.
    attached_checks: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, row: SeasonalPeriod, attached: int) -> SeasonalPeriodRead:
        return cls(
            id=row.id,
            name=row.name,
            kind=row.kind,
            start_month=row.start_month,
            start_day=row.start_day,
            end_month=row.end_month,
            end_day=row.end_day,
            start_date=row.start_date,
            end_date=row.end_date,
            series=row.series,
            note=row.note,
            attached_checks=attached,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


def _read(db: Session, row: SeasonalPeriod) -> SeasonalPeriodRead:
    return SeasonalPeriodRead.from_model(row, len(svc.attached_checks(db, row.id)))


@router.get("", response_model=list[SeasonalPeriodRead], summary="List the seasonal calendar")
def list_seasonal_periods(db: Annotated[Session, Depends(get_db)]) -> list[SeasonalPeriodRead]:
    counts = svc.attached_counts(db)
    return [SeasonalPeriodRead.from_model(r, counts.get(r.id, 0)) for r in svc.list_periods(db)]


@router.get("/{period_id}", response_model=SeasonalPeriodRead, summary="Read one entry")
def get_seasonal_period(
    period_id: uuid.UUID, db: Annotated[Session, Depends(get_db)]
) -> SeasonalPeriodRead:
    return _read(db, svc.get_period(db, period_id))


@router.post(
    "",
    response_model=SeasonalPeriodRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a calendar entry (admin)",
)
def create_seasonal_period(
    payload: SeasonalPeriodWrite,
    current_user: AdminUser,
    db: Annotated[Session, Depends(get_db)],
) -> SeasonalPeriodRead:
    row = svc.create_period(db, payload.model_dump(), actor_id=current_user.id)
    return _read(db, row)


@router.patch("/{period_id}", response_model=SeasonalPeriodRead, summary="Edit an entry (admin)")
def update_seasonal_period(
    period_id: uuid.UUID,
    payload: SeasonalPeriodPatch,
    current_user: AdminUser,
    db: Annotated[Session, Depends(get_db)],
) -> SeasonalPeriodRead:
    fields: dict[str, Any] = payload.model_dump(exclude_unset=True)
    row = svc.update_period(db, period_id, fields, actor_id=current_user.id)
    return _read(db, row)


@router.delete(
    "/{period_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an entry (admin; refused while any check names it)",
)
def delete_seasonal_period(
    period_id: uuid.UUID,
    current_user: AdminUser,
    db: Annotated[Session, Depends(get_db)],
) -> None:
    svc.delete_period(db, period_id, actor_id=current_user.id)
