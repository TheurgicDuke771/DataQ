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
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.db.models import Check
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


def _validate_kind(kind: str) -> None:
    if kind not in _V1_SUPPORTED_KINDS:
        raise CheckConfigInvalidError(
            f"check kind {kind!r} is not supported in v1; only 'expectation'",
            detail={"kind": kind, "supported": sorted(_V1_SUPPORTED_KINDS)},
        )


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
) -> Check:
    """Create a check in a suite.

    Raises `SuiteNotFoundError` (404) if the suite does not exist, or
    `CheckConfigInvalidError` (422) for an unsupported kind.
    """
    get_suite(session, suite_id)  # 404 if the suite is missing
    _validate_kind(kind)

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
) -> Check:
    """Partial update. `suite_id` and `kind` are immutable.

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
    session.commit()
    session.refresh(check)
    log.info("check_updated", check_id=str(check.id))
    return check


def delete_check(session: Session, suite_id: uuid.UUID, check_id: uuid.UUID) -> None:
    check = get_check(session, suite_id, check_id)
    session.delete(check)
    session.commit()
    log.info("check_deleted", check_id=str(check_id))
