"""Suite CRUD endpoints.

Thin HTTP layer over `suite_service`: validates request shapes, wires the
current user + db session, and maps models onto responses. All business logic
(connection validation, persistence) lives in the service. `connection_id` is
set at create and immutable thereafter (re-pointing would orphan child checks).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from backend.app.core.auth import get_current_user
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.services import suite_service as svc
from backend.app.services.suite_authz import require_permission

router = APIRouter(tags=["suites"])


class SuiteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1024)
    connection_id: uuid.UUID


class SuiteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1024)


class SuiteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    connection_id: uuid.UUID
    created_by: uuid.UUID


@router.post(
    "/suites",
    response_model=SuiteRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a suite",
)
def create_suite(
    payload: SuiteCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> SuiteRead:
    suite = svc.create_suite(
        db,
        name=payload.name,
        description=payload.description,
        connection_id=payload.connection_id,
        created_by=current_user.id,
    )
    return SuiteRead.model_validate(suite)


@router.get("/suites", response_model=list[SuiteRead], summary="List suites")
def list_suites(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    connection_id: uuid.UUID | None = None,
) -> list[SuiteRead]:
    # Scoped to suites the user owns or has a share on.
    suites = svc.list_suites(db, user_id=current_user.id, connection_id=connection_id)
    return [SuiteRead.model_validate(s) for s in suites]


@router.get("/suites/{suite_id}", response_model=SuiteRead, summary="Get a suite")
def get_suite(
    suite_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> SuiteRead:
    suite = require_permission(db, suite_id, current_user.id, minimum="view")
    return SuiteRead.model_validate(suite)


@router.patch("/suites/{suite_id}", response_model=SuiteRead, summary="Update a suite")
def update_suite(
    suite_id: uuid.UUID,
    payload: SuiteUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> SuiteRead:
    require_permission(db, suite_id, current_user.id, minimum="edit")
    suite = svc.update_suite(db, suite_id, name=payload.name, description=payload.description)
    return SuiteRead.model_validate(suite)


@router.delete(
    "/suites/{suite_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a suite",
)
def delete_suite(
    suite_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    require_permission(db, suite_id, current_user.id, minimum="admin")
    svc.delete_suite(db, suite_id)
