"""Admin coverage settings — sensitivity for automatic baselines (ADR 0047)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import ConfigDict
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel, ApiRequestModel, updated_by_email
from backend.app.core.auth import require_workspace_admin
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.services import coverage_settings_service as svc

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_workspace_admin)],
)

# Strict: `true` must not save as a 1-deviation tier, nor "yes" as seasonality on.
_STRICT = ConfigDict(extra="forbid", strict=True)


class SensitivityWrite(ApiRequestModel):
    """Standard deviations from the table's own history at which a baseline warns, fails
    and goes critical. All three are set together."""

    model_config = _STRICT

    warn: float
    fail: float
    critical: float


class BaselineShapeWrite(ApiRequestModel):
    """How much history a baseline compares against. All three are set together."""

    model_config = _STRICT

    window: int
    min_points: int
    seasonality: bool


class CoverageSettingsWrite(ApiRequestModel):
    """A level sets a group whole or leaves it to the level below: any of the three
    baselines' thresholds, and the shared baseline shape."""

    sensitivity: dict[svc.Baseline, SensitivityWrite] | None = None
    baseline: BaselineShapeWrite | None = None

    def document(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class EffectiveSensitivity(ApiModel):
    warn: float
    fail: float
    critical: float
    #: The level these three came from.
    source: svc.Level


class EffectiveShape(ApiModel):
    window: int
    min_points: int
    seasonality: bool
    source: svc.Level


class EffectiveCoverageRead(ApiModel):
    """What the hierarchy resolves to: asset override, then workspace setting, then the
    built-in default."""

    sensitivity: dict[svc.Baseline, EffectiveSensitivity]
    baseline: EffectiveShape


class CoverageSettingsRead(ApiModel):
    """The workspace's sensitivity for automatic baselines. `setting` is exactly what is
    stored (a group it omits falls to the built-in default); `effective` is the result for a
    table with no override of its own. `is_default` means nothing is stored."""

    setting: dict[str, Any]
    effective: EffectiveCoverageRead
    defaults: EffectiveCoverageRead
    is_default: bool
    updated_by: str | None
    updated_at: datetime | None


def effective_read(effective: svc.Effective) -> EffectiveCoverageRead:
    return EffectiveCoverageRead(
        sensitivity={
            baseline: EffectiveSensitivity(
                **effective.sensitivity[baseline].as_dict(),
                source=effective.sensitivity_from[baseline],
            )
            for baseline in svc.BASELINES
        },
        baseline=EffectiveShape(**effective.shape.as_dict(), source=effective.shape_from),
    )


def _read(db: Session) -> CoverageSettingsRead:
    row = svc.get_row(db)
    setting = dict(row.config) if row is not None else {}
    return CoverageSettingsRead(
        setting=setting,
        effective=effective_read(svc.resolve(setting)),
        defaults=effective_read(svc.resolve(None)),
        is_default=row is None,
        updated_by=updated_by_email(db, row),
        updated_at=row.updated_at if row is not None else None,
    )


@router.get(
    "/coverage", response_model=CoverageSettingsRead, summary="Automatic-baseline sensitivity"
)
def get_coverage_settings(db: Annotated[Session, Depends(get_db)]) -> CoverageSettingsRead:
    return _read(db)


@router.put(
    "/coverage",
    response_model=CoverageSettingsRead,
    summary="Set automatic-baseline sensitivity (admin)",
)
def put_coverage_settings(
    payload: CoverageSettingsWrite,
    current_user: Annotated[User, Depends(require_workspace_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> CoverageSettingsRead:
    svc.set_workspace(db, payload.document(), actor=current_user)
    db.commit()
    return _read(db)


@router.delete(
    "/coverage",
    response_model=CoverageSettingsRead,
    summary="Reset automatic-baseline sensitivity (admin)",
)
def reset_coverage_settings(
    current_user: Annotated[User, Depends(require_workspace_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> CoverageSettingsRead:
    svc.reset_workspace(db, actor=current_user)
    db.commit()
    return _read(db)
