"""Admin scoring settings — the health-score penalty weights (#1559)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel
from backend.app.core.auth import require_workspace_admin
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.services import scoring_settings_service as svc

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_workspace_admin)],
)


class ScoringWeightsRead(ApiModel):
    """The penalties applied per severity tier when a health score is computed.
    `pass` is always 0. `is_default` means no row is stored and the ADR 0005
    defaults apply. Scores are computed on read, so a change recolours every
    score at once, past and present."""

    warn: float
    fail: float
    critical: float
    is_default: bool
    defaults: dict[str, float]
    updated_by: str | None
    updated_at: datetime | None


class ScoringWeightsWrite(ApiModel):
    warn: float = Field(ge=0, le=svc.MAX_WEIGHT)
    fail: float = Field(ge=0, le=svc.MAX_WEIGHT)
    critical: float = Field(gt=0, le=svc.MAX_WEIGHT)


def _read(db: Session) -> ScoringWeightsRead:
    row = svc.get_row(db)
    w = svc.weights(db)
    updated_by = None
    if row is not None and row.updated_by is not None:
        user = db.get(User, row.updated_by)
        updated_by = user.email if user is not None else None
    d = svc.DEFAULT_WEIGHTS
    return ScoringWeightsRead(
        warn=w.warn,
        fail=w.fail,
        critical=w.critical,
        is_default=row is None,
        defaults={"warn": d.warn, "fail": d.fail, "critical": d.critical},
        updated_by=updated_by,
        updated_at=row.updated_at if row is not None else None,
    )


@router.get("/scoring", response_model=ScoringWeightsRead, summary="Health-score weights (admin)")
def get_scoring_weights(db: Annotated[Session, Depends(get_db)]) -> ScoringWeightsRead:
    return _read(db)


@router.put(
    "/scoring", response_model=ScoringWeightsRead, summary="Set health-score weights (admin)"
)
def put_scoring_weights(
    payload: ScoringWeightsWrite,
    current_user: Annotated[User, Depends(require_workspace_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> ScoringWeightsRead:
    svc.set_weights(
        db, warn=payload.warn, fail=payload.fail, critical=payload.critical, actor=current_user
    )
    db.commit()
    return _read(db)


@router.delete(
    "/scoring", response_model=ScoringWeightsRead, summary="Reset health-score weights (admin)"
)
def reset_scoring_weights(
    current_user: Annotated[User, Depends(require_workspace_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> ScoringWeightsRead:
    svc.reset_weights(db, actor=current_user)
    db.commit()
    return _read(db)
