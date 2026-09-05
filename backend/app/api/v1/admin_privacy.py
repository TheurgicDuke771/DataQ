"""Admin privacy settings — the zero-sample toggle (#1887)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel
from backend.app.core.auth import require_workspace_admin
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.services import privacy_settings_service as svc

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_workspace_admin)],
)


class PrivacySettingsRead(ApiModel):
    """`effective` is what every sample writer obeys (env floor OR the stored value);
    `stored` is only what the toggle last wrote. `env_forced` means the toggle can
    turn the mode on but not off."""

    effective: bool
    stored: bool
    source: Literal["env", "db", "off"]
    env_forced: bool
    updated_by: str | None
    updated_at: datetime | None


class PrivacySettingsWrite(ApiModel):
    zero_sample_mode: bool


def _read(db: Session) -> PrivacySettingsRead:
    row = svc.get_row(db)
    updated_by = None
    if row is not None and row.updated_by is not None:
        user = db.get(User, row.updated_by)
        updated_by = user.email if user is not None else None
    return PrivacySettingsRead(
        effective=svc.zero_sample_mode(db),
        stored=svc.stored_zero_sample_mode(db),
        source=svc.source(db),
        env_forced=svc.env_forced(),
        updated_by=updated_by,
        updated_at=row.updated_at if row is not None else None,
    )


@router.get("/privacy", response_model=PrivacySettingsRead, summary="Zero-sample mode (admin)")
def get_privacy_settings(db: Annotated[Session, Depends(get_db)]) -> PrivacySettingsRead:
    return _read(db)


@router.put("/privacy", response_model=PrivacySettingsRead, summary="Set zero-sample mode (admin)")
def put_privacy_settings(
    payload: PrivacySettingsWrite,
    current_user: Annotated[User, Depends(require_workspace_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> PrivacySettingsRead:
    svc.set_zero_sample_mode(db, enabled=payload.zero_sample_mode, actor=current_user)
    db.commit()
    return _read(db)
