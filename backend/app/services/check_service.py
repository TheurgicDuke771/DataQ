"""Check CRUD — checks are GX expectations nested under a suite.

A check belongs to exactly one suite (FK + cascade). This layer validates the
suite exists, enforces the v1 monitor-kind limit, and treats the check's
`config` (the GX expectation kwargs) as free-form JSONB — per-expectation
schema validation against live data is the check dry-run path (a later Week-3
task), not CRUD.

v1 monitor-kind limit (ADR 0012): although the schema CHECK reserves
`freshness / volume / schema_drift / anomaly / comparison`, v1 only *runs*
`expectation`. The API therefore refuses to author a non-`expectation` check —
a reserved kind is schema-valid for forward-compat but not yet runnable, so
letting a user create one would just produce a check that can never execute.

FastAPI-free like the sibling services: takes a `Session`, returns ORM models,
raises `DataQError` subclasses.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.db.models import Check, CheckVersion, Connection, Result, Run, Suite
from backend.app.services.custom_sql import is_custom_sql, validate_custom_sql_check
from backend.app.services.suite_service import get_suite

log = get_logger(__name__)

# v1 authors only GX expectations; the other reserved kinds (ADR 0012) are
# schema-valid but have no runner yet, so CRUD refuses them.
_V1_SUPPORTED_KINDS = {"expectation"}


class CheckNotFoundError(DataQError):
    status_code = 404
    code = "check_not_found"


class CheckConfigInvalidError(DataQError):
    status_code = 422
    code = "check_config_invalid"


def _connection_type(session: Session, suite: Suite) -> str:
    """The datasource type of the suite's connection — for custom-SQL gating.

    The suite's `connection_id` FK is NOT NULL, so the connection always exists.
    """
    connection = session.get(Connection, suite.connection_id)
    assert connection is not None
    return connection.type


def validate_kind(kind: str) -> None:
    """Reject a non-`expectation` kind (422). Shared by CRUD and suite import."""
    if kind not in _V1_SUPPORTED_KINDS:
        raise CheckConfigInvalidError(
            f"check kind {kind!r} is not supported in v1; only 'expectation'",
            detail={"kind": kind, "supported": sorted(_V1_SUPPORTED_KINDS)},
        )


def record_check_version(
    session: Session, check: Check, *, actor_id: uuid.UUID | None
) -> CheckVersion:
    """Append an immutable snapshot of `check`'s current state as its next
    version (a per-check sequence starting at 1). The caller commits — this only
    adds the row, so the snapshot and the create/update it records commit
    atomically. The `(check_id, version_no)` unique constraint is the backstop
    against a concurrent double-write computing the same number (rare under v1's
    single-tenant, low-concurrency editing).

    `check.id` must be populated (flush or commit the check first).
    """
    # MAX over no rows is NULL → None; `or 0` makes the first version 1.
    current_max = session.scalar(
        select(func.max(CheckVersion.version_no)).where(CheckVersion.check_id == check.id)
    )
    next_no = (current_max or 0) + 1
    version = CheckVersion(
        check_id=check.id,
        version_no=next_no,
        name=check.name,
        kind=check.kind,
        expectation_type=check.expectation_type,
        config=check.config,
        warn_threshold=check.warn_threshold,
        fail_threshold=check.fail_threshold,
        critical_threshold=check.critical_threshold,
        changed_by=actor_id,
    )
    session.add(version)
    return version


def create_check(
    session: Session,
    *,
    suite_id: uuid.UUID,
    name: str,
    kind: str,
    expectation_type: str,
    config: dict[str, Any],
    warn_threshold: Decimal | None,
    fail_threshold: Decimal | None,
    critical_threshold: Decimal | None,
    actor_id: uuid.UUID | None = None,
) -> Check:
    """Create a check in a suite, recording its first version (#280).

    Raises `SuiteNotFoundError` (404) if the suite does not exist, or
    `CheckConfigInvalidError` (422) for an unsupported kind.
    """
    suite = get_suite(session, suite_id)  # 404 if the suite is missing
    validate_kind(kind)
    if is_custom_sql(expectation_type):
        validate_custom_sql_check(
            expectation_type=expectation_type,
            config=config,
            connection_type=_connection_type(session, suite),
        )

    check = Check(
        suite_id=suite_id,
        name=name,
        kind=kind,
        expectation_type=expectation_type,
        config=config,
        warn_threshold=warn_threshold,
        fail_threshold=fail_threshold,
        critical_threshold=critical_threshold,
    )
    session.add(check)
    session.flush()  # assign check.id so the v1 snapshot can reference it
    record_check_version(session, check, actor_id=actor_id)
    session.commit()
    session.refresh(check)
    log.info("check_created", check_id=str(check.id), suite_id=str(suite_id))
    return check


def list_checks(session: Session, suite_id: uuid.UUID) -> list[Check]:
    """List a suite's checks (404 if the suite does not exist)."""
    get_suite(session, suite_id)
    stmt = select(Check).where(Check.suite_id == suite_id).order_by(Check.created_at)
    return list(session.scalars(stmt))


def get_check(session: Session, suite_id: uuid.UUID, check_id: uuid.UUID) -> Check:
    """Fetch a check, enforcing that it belongs to `suite_id` (else 404)."""
    check = session.get(Check, check_id)
    if check is None or check.suite_id != suite_id:
        raise CheckNotFoundError(
            "check not found",
            detail={"suite_id": str(suite_id), "check_id": str(check_id)},
        )
    return check


def update_check(
    session: Session,
    suite_id: uuid.UUID,
    check_id: uuid.UUID,
    *,
    name: str | None = None,
    expectation_type: str | None = None,
    config: dict[str, Any] | None = None,
    warn_threshold: Decimal | None = None,
    fail_threshold: Decimal | None = None,
    critical_threshold: Decimal | None = None,
    actor_id: uuid.UUID | None = None,
) -> Check:
    """Partial update, snapshotting the post-update state as a new version (#280).

    Follows the codebase PATCH convention (connections / suites): a `None`
    argument means "not provided", so an omitted field is left unchanged. v1 has
    no clear-to-NULL path for thresholds; recreate the check to drop one.
    """
    check = get_check(session, suite_id, check_id)
    if name is not None:
        check.name = name
    if expectation_type is not None:
        check.expectation_type = expectation_type
    if config is not None:
        check.config = config
    if warn_threshold is not None:
        check.warn_threshold = warn_threshold
    if fail_threshold is not None:
        check.fail_threshold = fail_threshold
    if critical_threshold is not None:
        check.critical_threshold = critical_threshold
    # Re-validate against the post-patch state: a PATCH may change only the query
    # (config) or only the type, so guard the effective custom-SQL check before
    # commit (a rejected update persists nothing).
    if is_custom_sql(check.expectation_type):
        suite = get_suite(session, suite_id)
        validate_custom_sql_check(
            expectation_type=check.expectation_type,
            config=check.config,
            connection_type=_connection_type(session, suite),
        )
    # Only snapshot a real change: a no-op PATCH (empty body, or fields set to
    # their current values) must not mint a duplicate version — that would fill
    # the history drawer with noise and defeat "see previous config". SQLAlchemy
    # reports net changes, so setting a field to its existing value isn't dirty.
    if session.is_modified(check):
        record_check_version(session, check, actor_id=actor_id)
    session.commit()
    session.refresh(check)
    log.info("check_updated", check_id=str(check.id))
    return check


def delete_check(session: Session, suite_id: uuid.UUID, check_id: uuid.UUID) -> None:
    check = get_check(session, suite_id, check_id)
    session.delete(check)
    session.commit()
    log.info("check_deleted", check_id=str(check_id))


def list_check_versions(
    session: Session, suite_id: uuid.UUID, check_id: uuid.UUID
) -> list[CheckVersion]:
    """A check's version history, newest first (#280). 404 if the check is
    missing or doesn't belong to `suite_id`. Eager-loads each version's author
    (only query that needs it) so the API can name the editor without an N+1.
    """
    get_check(session, suite_id, check_id)  # 404 / cross-suite guard
    return list(
        session.scalars(
            select(CheckVersion)
            .where(CheckVersion.check_id == check_id)
            .options(selectinload(CheckVersion.author))
            .order_by(CheckVersion.version_no.desc())
        )
    )


@dataclass(frozen=True)
class CheckResultPoint:
    """One past result for a check — the trend datum behind the per-check chart."""

    run_id: uuid.UUID
    status: str
    metric_value: float | None
    created_at: datetime


def list_check_result_history(
    session: Session, suite_id: uuid.UUID, check_id: uuid.UUID, *, limit: int = 30
) -> list[CheckResultPoint]:
    """A check's recent results in chronological order (oldest→newest) for the
    per-check trend (ADR 0022). 404 if the check is missing or cross-suite.

    Takes the latest `limit` results (newest-first in SQL, then reversed) so the
    chart shows the most recent window left-to-right. `metric_value` is the
    SQL-aggregatable scalar a run measured (ADR 0012); `None` for checks that
    record no metric. Suite scoping is the caller's (router `require_permission`);
    the Run join only guards against a result leaking across suites.
    """
    get_check(session, suite_id, check_id)  # 404 / cross-suite guard
    stmt = (
        select(Result.run_id, Result.status, Result.metric_value, Run.created_at)
        .join(Run, Result.run_id == Run.id)
        .where(Result.check_id == check_id, Run.suite_id == suite_id)
        .order_by(Run.created_at.desc())
        .limit(limit)
    )
    rows = [
        CheckResultPoint(
            run_id=run_id,
            status=status,
            metric_value=float(metric_value) if metric_value is not None else None,
            created_at=created_at,
        )
        for run_id, status, metric_value, created_at in session.execute(stmt)
    ]
    rows.reverse()  # chronological for the chart x-axis
    return rows
