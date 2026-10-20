"""Browser-reported usage events (ADR 0050). The server is the only emitter: the SPA never
talks to a collector, it posts a name + attributes here and this route validates them against
the same closed vocabulary every server-side event goes through."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import ConfigDict, Field
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel, ApiRequestModel
from backend.app.core import usage_telemetry
from backend.app.core.auth import get_current_user
from backend.app.core.errors import DataQError
from backend.app.core.usage_telemetry import CLIENT_EVENTS, UsageEvent, UsageEventInvalidError
from backend.app.db.session import get_db
from backend.app.services import telemetry_settings_service as svc

router = APIRouter(
    prefix="/telemetry", tags=["telemetry"], dependencies=[Depends(get_current_user)]
)

MAX_EVENTS_PER_POST = 20


class TelemetryEventInvalidError(DataQError):
    code = "telemetry_event_invalid"
    status_code = 422


class TelemetryStatusRead(ApiModel):
    """Whether the browser should report at all, and the only events it may report."""

    enabled: bool
    events: list[str]


class ClientEvent(ApiRequestModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(max_length=64)
    attributes: dict[str, str | int] = Field(default_factory=dict)


class ClientEventsWrite(ApiRequestModel):
    events: list[ClientEvent] = Field(min_length=1, max_length=MAX_EVENTS_PER_POST)


class ClientEventsAccepted(ApiModel):
    accepted: int
    enabled: bool


def _client_event(name: str) -> UsageEvent:
    try:
        event = UsageEvent(name)
    except ValueError:
        raise TelemetryEventInvalidError("unknown usage event") from None
    if event not in CLIENT_EVENTS:
        raise TelemetryEventInvalidError(f"{event.value} is not a browser-reported event")
    return event


@router.get("", response_model=TelemetryStatusRead, summary="Usage-telemetry status")
def get_status(db: Annotated[Session, Depends(get_db)]) -> TelemetryStatusRead:
    return TelemetryStatusRead(
        enabled=svc.effective(db), events=sorted(e.value for e in CLIENT_EVENTS)
    )


@router.post(
    "/events",
    response_model=ClientEventsAccepted,
    status_code=202,
    summary="Report browser usage events",
)
def post_events(
    payload: ClientEventsWrite, db: Annotated[Session, Depends(get_db)]
) -> ClientEventsAccepted:
    validated: list[tuple[UsageEvent, dict[str, Any]]] = []
    for item in payload.events:
        event = _client_event(item.name)
        try:
            validated.append((event, usage_telemetry.validate(event, item.attributes)))
        except UsageEventInvalidError as exc:
            raise TelemetryEventInvalidError(str(exc)) from None
    enabled = svc.effective(db)
    for event, attributes in validated:
        usage_telemetry.emit(event, session=db, **attributes)
    return ClientEventsAccepted(accepted=len(validated), enabled=enabled)
