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

from fastapi import APIRouter, Depends, Query, status
from pydantic import ConfigDict, Field
from sqlalchemy.orm import Session

from backend.app.api.v1._base import ApiModel
from backend.app.core.auth import get_current_user
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore, get_secret_store
from backend.app.datasources.monitors import STATEFUL_MONITOR_KINDS
from backend.app.db.models import Connection, User
from backend.app.db.session import get_db
from backend.app.services import check_service as svc
from backend.app.services import dryrun_service as dryrun
from backend.app.services import schema_drift as schema_drift_service
from backend.app.services.check_service import CheckConfigInvalidError
from backend.app.services.suite_authz import require_permission

log = get_logger(__name__)

router = APIRouter(tags=["checks"])


class CheckCreate(ApiModel):
    name: str = Field(min_length=1, max_length=256)
    # Authorable kinds: expectation, freshness/volume, comparison (service
    # enforces; remaining reserved kinds 422).
    kind: str = "expectation"
    expectation_type: str = Field(min_length=1, max_length=128)
    config: dict[str, Any] = Field(default_factory=dict)
    # Comparison source ref (ADR 0015) — required for kind='comparison',
    # rejected on any other kind (service enforces).
    source_connection_id: uuid.UUID | None = None
    warn_threshold: Decimal | None = None
    fail_threshold: Decimal | None = None
    critical_threshold: Decimal | None = None


class CheckUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    expectation_type: str | None = Field(default=None, min_length=1, max_length=128)
    config: dict[str, Any] | None = None
    # Repoint a comparison check's source (never clearable — the kind requires
    # it); 422 on any other kind.
    source_connection_id: uuid.UUID | None = None
    warn_threshold: Decimal | None = None
    fail_threshold: Decimal | None = None
    critical_threshold: Decimal | None = None


class CheckRead(ApiModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    suite_id: uuid.UUID
    name: str
    kind: str
    expectation_type: str
    source_connection_id: uuid.UUID | None = None
    config: dict[str, Any]
    warn_threshold: float | None
    fail_threshold: float | None
    critical_threshold: float | None
    # Alert snooze (suppression): when in the future, the check's alerts are muted
    # until then; NULL / past = active. Set via the snooze endpoints, not PATCH.
    alert_snoozed_until: datetime | None = None


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
        source_connection_id=payload.source_connection_id,
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
        source_connection_id=payload.source_connection_id,
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


# ───────────────────────── schema-drift re-baseline (#592) ─────────


@router.post(
    "/suites/{suite_id}/checks/{check_id}/rebaseline",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Drop a schema_drift check's stored baseline (recaptured on the next run)",
)
def rebaseline_check(
    suite_id: uuid.UUID,
    check_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """Deliberately a delete-then-recapture-next-run, not an immediate recapture:
    recapturing here would run datasource introspection on the API request
    thread. 204 whether or not a baseline existed (idempotent); 422 for a
    non-stateful kind (nothing to re-baseline)."""
    require_permission(db, suite_id, current_user.id, minimum="edit")
    check = svc.get_check(db, suite_id, check_id)
    if check.kind not in STATEFUL_MONITOR_KINDS:
        raise CheckConfigInvalidError(
            f"only stateful monitor checks hold a baseline; {check.kind!r} does not",
            detail={"kind": check.kind},
        )
    schema_drift_service.rebaseline(db, check)
    db.commit()
    log.info("check_rebaselined", check_id=str(check_id), suite_id=str(suite_id))


# ───────────────────────── alert snooze (suppression) ──────────────


class CheckSnoozeRequest(ApiModel):
    # Cap at 30 days so a typo can't mute a check effectively forever.
    hours: float = Field(gt=0, le=720, description="Mute the check's alerts for this many hours")


@router.post(
    "/suites/{suite_id}/checks/{check_id}/snooze",
    response_model=CheckRead,
    summary="Snooze a check's alerts for N hours",
)
def snooze_check(
    suite_id: uuid.UUID,
    check_id: uuid.UUID,
    payload: CheckSnoozeRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CheckRead:
    require_permission(db, suite_id, current_user.id, minimum="edit")
    check = svc.snooze_check(db, suite_id, check_id, hours=payload.hours)
    return CheckRead.model_validate(check)


@router.delete(
    "/suites/{suite_id}/checks/{check_id}/snooze",
    response_model=CheckRead,
    summary="Clear a check's alert snooze",
)
def clear_check_snooze(
    suite_id: uuid.UUID,
    check_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> CheckRead:
    require_permission(db, suite_id, current_user.id, minimum="edit")
    check = svc.clear_check_snooze(db, suite_id, check_id)
    return CheckRead.model_validate(check)


# ───────────────────────── version history (#280) ──────────────────


class CheckVersionRead(ApiModel):
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
    source_connection_id: uuid.UUID | None = None
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


# ───────────────────────── result history (trend, ADR 0022) ─────────


class CheckResultPointRead(ApiModel):
    """One past result for a check — the per-check trend datum (metric over time)."""

    model_config = ConfigDict(from_attributes=True)

    run_id: uuid.UUID
    status: str
    metric_value: float | None
    created_at: datetime


@router.get(
    "/suites/{suite_id}/checks/{check_id}/history",
    response_model=list[CheckResultPointRead],
    summary="A check's recent result history (chronological) for the trend chart",
)
def list_check_result_history(
    suite_id: uuid.UUID,
    check_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=180)] = 30,
) -> list[CheckResultPointRead]:
    require_permission(db, suite_id, current_user.id, minimum="view")
    return [
        CheckResultPointRead.model_validate(p)
        for p in svc.list_check_result_history(db, suite_id, check_id, limit=limit)
    ]


# ───────────────────────── dry-run (preview, no persistence) ────────


class CheckDryRunRequest(ApiModel):
    kind: str = "expectation"
    expectation_type: str = Field(min_length=1, max_length=128)
    config: dict[str, Any] = Field(default_factory=dict)
    warn_threshold: Decimal | None = None
    fail_threshold: Decimal | None = None
    critical_threshold: Decimal | None = None
    # The target comes from the suite's own run target (#215/#532) — resolved
    # server-side exactly like a persisted run, so the preview runs against what a
    # saved run would (and flat-file `path` / UC `catalog` / batch resolution are
    # handled for free). No client-supplied table.


class CheckDryRunResult(ApiModel):
    status: str  # pass | warn | fail | critical (ADR 0005) | error (#122)
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
        target=suite.target,
        secret_store=secret_store,
    )
    return CheckDryRunResult(
        status=outcome.status,
        # Explicit Decimal→float: the response model wants clean JSON numbers
        # (pydantic coerced this implicitly; typed now so checkers see it too).
        metric_value=float(outcome.metric_value) if outcome.metric_value is not None else None,
        observed_value=outcome.observed_value,
        expected_value=outcome.expected_value,
    )
