"""Per-suite alert notification config — nested under a suite.

`GET`/`PUT`/`DELETE /suites/{suite_id}/notifications`. View to read, edit to
change (the capability ladder). The webhook URL is write-only (a secret): it's
accepted on `PUT` and written through the SecretStore, but never returned — the
read surface exposes only ``has_webhook``.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel
from backend.app.core.auth import get_current_user
from backend.app.core.secrets import SecretStore, get_secret_store
from backend.app.db.models import SuiteNotification, User
from backend.app.db.session import get_db
from backend.app.services import notification_service as svc
from backend.app.services.suite_authz import require_permission

router = APIRouter(tags=["notifications"])


class SuiteNotificationRead(ApiModel):
    """A suite's effective notification config. ``configured`` distinguishes a
    saved row from the defaults a suite falls back to. The webhook URL is never
    returned — only whether one is set (``has_webhook``)."""

    configured: bool
    enabled: bool
    alert_on: str
    has_webhook: bool


class SuiteNotificationUpdate(ApiModel):
    enabled: bool = True
    # Default 'warn' matches the no-config fallback, so an omitted threshold
    # doesn't silently tighten delivery (a saved config keeps the prior behaviour).
    alert_on: Literal["fail", "warn", "always"] = "warn"
    # Tri-state: omit/null = leave the stored webhook unchanged; "" = clear (use
    # the workspace webhook); a URL = set the per-suite webhook (write-only). The
    # https check lives in the service (a clean DataQError 422), not a Pydantic
    # validator (whose ValueError ctx isn't JSON-serializable by our handler).
    webhook: str | None = None


def _read(config: SuiteNotification | None) -> SuiteNotificationRead:
    if config is None:
        return SuiteNotificationRead(
            configured=False,
            enabled=True,
            alert_on=svc.DEFAULT_ALERT_ON,
            has_webhook=False,
        )
    return SuiteNotificationRead(
        configured=True,
        enabled=config.enabled,
        alert_on=config.alert_on,
        has_webhook=config.webhook_secret_ref is not None,
    )


@router.get(
    "/suites/{suite_id}/notifications",
    response_model=SuiteNotificationRead,
    summary="Get a suite's notification config",
)
def get_notifications(
    suite_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> SuiteNotificationRead:
    require_permission(db, suite_id, current_user.id, minimum="view")
    return _read(svc.get_config(db, suite_id))


@router.put(
    "/suites/{suite_id}/notifications",
    response_model=SuiteNotificationRead,
    summary="Create or update a suite's notification config",
)
def put_notifications(
    suite_id: uuid.UUID,
    payload: SuiteNotificationUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
) -> SuiteNotificationRead:
    require_permission(db, suite_id, current_user.id, minimum="edit")
    config = svc.upsert_config(
        db,
        suite_id=suite_id,
        enabled=payload.enabled,
        alert_on=payload.alert_on,
        webhook=payload.webhook,
        secret_store=secret_store,
    )
    return _read(config)


@router.delete(
    "/suites/{suite_id}/notifications",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a suite's notification config (revert to defaults)",
)
def delete_notifications(
    suite_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
) -> None:
    require_permission(db, suite_id, current_user.id, minimum="edit")
    svc.delete_config(db, suite_id, secret_store=secret_store)
