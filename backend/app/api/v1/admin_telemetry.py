"""Admin usage-telemetry settings — the opt-in and the event vocabulary (ADR 0050)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel, ApiRequestModel, updated_by_email
from backend.app.core import usage_telemetry
from backend.app.core.auth import require_workspace_admin
from backend.app.core.config import get_settings
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.services import telemetry_settings_service as svc

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_workspace_admin)],
)


class TelemetryEventRead(ApiModel):
    name: str
    #: Each attribute's closed vocabulary, or `"integer"` for a count.
    attributes: dict[str, list[str] | str]


class TelemetrySettingsRead(ApiModel):
    """`enabled` is the EFFECTIVE state: the stored opt-in AND an operator-configured sink.
    `stored` is only what the toggle last wrote; with `sink = off` it sends nothing, and the
    page says so rather than reporting a state the emitter ignores. `events` is the whole
    vocabulary — the only values that can ever leave the deployment."""

    enabled: bool
    stored: bool
    sink: Literal["off", "file", "otlp"]
    events: list[TelemetryEventRead]
    updated_by: str | None
    updated_at: datetime | None


class TelemetrySettingsWrite(ApiRequestModel):
    enabled: bool


def _read(db: Session) -> TelemetrySettingsRead:
    row = svc.get_row(db)
    events: list[dict[str, Any]] = usage_telemetry.describe_events()
    return TelemetrySettingsRead(
        enabled=svc.effective(db),
        stored=svc.stored_enabled(db),
        sink=get_settings().telemetry_sink,
        events=[TelemetryEventRead(**event) for event in events],
        updated_by=updated_by_email(db, row),
        updated_at=row.updated_at if row is not None else None,
    )


@router.get("/telemetry", response_model=TelemetrySettingsRead, summary="Usage telemetry (admin)")
def get_telemetry_settings(db: Annotated[Session, Depends(get_db)]) -> TelemetrySettingsRead:
    return _read(db)


@router.put(
    "/telemetry", response_model=TelemetrySettingsRead, summary="Set the usage-telemetry opt-in"
)
def put_telemetry_settings(
    payload: TelemetrySettingsWrite,
    current_user: Annotated[User, Depends(require_workspace_admin)],
    db: Annotated[Session, Depends(get_db)],
) -> TelemetrySettingsRead:
    svc.set_enabled(db, enabled=payload.enabled, actor=current_user)
    db.commit()
    svc.invalidate()
    return _read(db)
