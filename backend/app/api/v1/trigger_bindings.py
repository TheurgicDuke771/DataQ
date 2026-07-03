"""Trigger-binding CRUD endpoints — manage suite-run triggers (provider-agnostic).

Thin HTTP layer over `trigger_binding_service`: a binding maps a successful
orchestrator run (`provider`/`pipeline_or_dag_id`/`env`) to a `suite_id`. All
validation, suite-permission gating, and conflict handling live in the service.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import ConfigDict, Field
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel
from backend.app.core.auth import get_current_user
from backend.app.db.models import TriggerBinding, User
from backend.app.db.session import get_db
from backend.app.services import trigger_binding_service as svc

router = APIRouter(tags=["trigger-bindings"])


class TriggerBindingCreate(ApiModel):
    provider: str
    pipeline_or_dag_id: str = Field(min_length=1, max_length=256)
    env: str
    suite_id: uuid.UUID
    enabled: bool = True


class TriggerBindingUpdate(ApiModel):
    enabled: bool


class TriggerBindingRead(ApiModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: str
    pipeline_or_dag_id: str
    env: str
    suite_id: uuid.UUID
    enabled: bool


@router.post(
    "/trigger-bindings",
    response_model=TriggerBindingRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a trigger binding",
)
def create_trigger_binding(
    payload: TriggerBindingCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> TriggerBinding:
    return svc.create_binding(
        db,
        provider=payload.provider,
        pipeline_or_dag_id=payload.pipeline_or_dag_id,
        env=payload.env,
        suite_id=payload.suite_id,
        user_id=current_user.id,
        enabled=payload.enabled,
    )


@router.get(
    "/trigger-bindings",
    response_model=list[TriggerBindingRead],
    summary="List trigger bindings on accessible suites",
)
def list_trigger_bindings(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    provider: str | None = None,
    env: str | None = None,
    suite_id: uuid.UUID | None = None,
) -> list[TriggerBinding]:
    return svc.list_bindings(
        db, user_id=current_user.id, provider=provider, env=env, suite_id=suite_id
    )


@router.get(
    "/trigger-bindings/{binding_id}",
    response_model=TriggerBindingRead,
    summary="Get a trigger binding",
)
def get_trigger_binding(
    binding_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> TriggerBinding:
    return svc.get_binding(db, binding_id, user_id=current_user.id)


@router.patch(
    "/trigger-bindings/{binding_id}",
    response_model=TriggerBindingRead,
    summary="Enable or disable a trigger binding",
)
def update_trigger_binding(
    binding_id: uuid.UUID,
    payload: TriggerBindingUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> TriggerBinding:
    return svc.update_binding(db, binding_id, user_id=current_user.id, enabled=payload.enabled)


@router.delete(
    "/trigger-bindings/{binding_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a trigger binding",
)
def delete_trigger_binding(
    binding_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    svc.delete_binding(db, binding_id, user_id=current_user.id)
