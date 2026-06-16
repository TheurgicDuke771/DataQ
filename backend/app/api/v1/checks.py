"""Check CRUD endpoints — GX expectations nested under a suite.

Routes are nested (`/suites/{suite_id}/checks/...`) so every check operation is
scoped to its suite (and, once suite-sharing lands, authorized once at the suite
level). Thin layer over `check_service`.

Threshold typing: requests accept `Decimal` so values land exactly in the
`Numeric` columns (a JSON float → unbounded NUMERIC would pollute precision);
responses emit `float` for clean JSON numbers, coerced from the stored Decimal.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from backend.app.core.auth import get_current_user
from backend.app.core.secrets import SecretStore, get_secret_store
from backend.app.db.models import Connection, User
from backend.app.db.session import get_db
from backend.app.services import check_service as svc
from backend.app.services import dryrun_service as dryrun
from backend.app.services.suite_authz import require_permission

router = APIRouter(tags=["checks"])


class CheckCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    # v1 authors only 'expectation' (service enforces; reserved kinds 422).
    kind: str = "expectation"
    expectation_type: str = Field(min_length=1, max_length=128)
    config: dict[str, Any] = Field(default_factory=dict)
    warn_threshold: Decimal | None = None
    fail_threshold: Decimal | None = None
    critical_threshold: Decimal | None = None


class CheckUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    expectation_type: str | None = Field(default=None, min_length=1, max_length=128)
    config: dict[str, Any] | None = None
    warn_threshold: Decimal | None = None
    fail_threshold: Decimal | None = None
    critical_threshold: Decimal | None = None


class CheckRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    suite_id: uuid.UUID
    name: str
    kind: str
    expectation_type: str
    config: dict[str, Any]
    warn_threshold: float | None
    fail_threshold: float | None
    critical_threshold: float | None


@router.post(
    "/suites/{suite_id}/checks",
    response_model=CheckRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a check in a suite",
)
def create_check(
    suite_id: uuid.UUID,
    payload: CheckCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CheckRead:
    require_permission(db, suite_id, current_user.id, minimum="edit")
    check = svc.create_check(
        db,
        suite_id=suite_id,
        name=payload.name,
        kind=payload.kind,
        expectation_type=payload.expectation_type,
        config=payload.config,
        warn_threshold=payload.warn_threshold,
        fail_threshold=payload.fail_threshold,
        critical_threshold=payload.critical_threshold,
        actor_id=current_user.id,
    )
    return CheckRead.model_validate(check)


@router.get(
    "/suites/{suite_id}/checks",
    response_model=list[CheckRead],
    summary="List a suite's checks",
)
def list_checks(
    suite_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[CheckRead]:
    require_permission(db, suite_id, current_user.id, minimum="view")
    return [CheckRead.model_validate(c) for c in svc.list_checks(db, suite_id)]


@router.get(
    "/suites/{suite_id}/checks/{check_id}",
    response_model=CheckRead,
    summary="Get a check",
)
def get_check(
    suite_id: uuid.UUID,
    check_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CheckRead:
    require_permission(db, suite_id, current_user.id, minimum="view")
    return CheckRead.model_validate(svc.get_check(db, suite_id, check_id))


@router.patch(
    "/suites/{suite_id}/checks/{check_id}",
    response_model=CheckRead,
    summary="Update a check",
)
def update_check(
    suite_id: uuid.UUID,
    check_id: uuid.UUID,
    payload: CheckUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CheckRead:
    require_permission(db, suite_id, current_user.id, minimum="edit")
    check = svc.update_check(
        db,
        suite_id,
        check_id,
        name=payload.name,
        expectation_type=payload.expectation_type,
        config=payload.config,
        warn_threshold=payload.warn_threshold,
        fail_threshold=payload.fail_threshold,
        critical_threshold=payload.critical_threshold,
        actor_id=current_user.id,
    )
    return CheckRead.model_validate(check)


@router.delete(
    "/suites/{suite_id}/checks/{check_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a check",
)
def delete_check(
    suite_id: uuid.UUID,
    check_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    require_permission(db, suite_id, current_user.id, minimum="edit")
    svc.delete_check(db, suite_id, check_id)


# ───────────────────────── version history (#280) ──────────────────


class CheckVersionRead(BaseModel):
    """One snapshot in a check's history. Like `CheckRead`, thresholds are coerced
    Decimal→float by Pydantic; `changed_by_name` (the author's display name or
    email, NULL for a system actor / removed user) comes from the model property,
    resolved server-side so the drawer needn't join users.
    """

    model_config = ConfigDict(from_attributes=True)

    version_no: int
    name: str
    kind: str
    expectation_type: str
    config: dict[str, Any]
    warn_threshold: float | None
    fail_threshold: float | None
    critical_threshold: float | None
    changed_by: uuid.UUID | None
    changed_by_name: str | None
    created_at: datetime


@router.get(
    "/suites/{suite_id}/checks/{check_id}/versions",
    response_model=list[CheckVersionRead],
    summary="List a check's version history (newest first)",
)
def list_check_versions(
    suite_id: uuid.UUID,
    check_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[CheckVersionRead]:
    require_permission(db, suite_id, current_user.id, minimum="view")
    return [
        CheckVersionRead.model_validate(v) for v in svc.list_check_versions(db, suite_id, check_id)
    ]


# ───────────────────────── dry-run (preview, no persistence) ────────


class CheckDryRunRequest(BaseModel):
    kind: str = "expectation"
    expectation_type: str = Field(min_length=1, max_length=128)
    config: dict[str, Any] = Field(default_factory=dict)
    warn_threshold: Decimal | None = None
    fail_threshold: Decimal | None = None
    critical_threshold: Decimal | None = None
    table: str = Field(min_length=1, description="Target table the check runs against")
    schema_: str | None = Field(default=None, alias="schema")


class CheckDryRunResult(BaseModel):
    status: str  # pass | warn | fail | critical (ADR 0005)
    metric_value: float | None
    observed_value: dict[str, Any] | None
    expected_value: dict[str, Any] | None


@router.post(
    "/suites/{suite_id}/checks/dryrun",
    response_model=CheckDryRunResult,
    summary="Dry-run a check against live data (no persistence)",
)
def dry_run_check(
    suite_id: uuid.UUID,
    payload: CheckDryRunRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
) -> CheckDryRunResult:
    # sync def → threadpool; the datasource connect + GX run are blocking.
    # Authoring action → 'edit'. The suite's connection FK is RESTRICT, so it
    # always resolves.
    suite = require_permission(db, suite_id, current_user.id, minimum="edit")
    connection = db.get(Connection, suite.connection_id)
    assert connection is not None
    outcome = dryrun.dry_run_check(
        connection,
        kind=payload.kind,
        expectation_type=payload.expectation_type,
        config=payload.config,
        warn_threshold=payload.warn_threshold,
        fail_threshold=payload.fail_threshold,
        critical_threshold=payload.critical_threshold,
        table=payload.table,
        schema=payload.schema_,
        secret_store=secret_store,
    )
    return CheckDryRunResult(
        status=outcome.status,
        metric_value=outcome.metric_value,
        observed_value=outcome.observed_value,
        expected_value=outcome.expected_value,
    )
