"""Suite CRUD — datasource-type-agnostic, FastAPI-free."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.datasources.sampling import (
    SAMPLING_ROW_COUNT_CONFLICT,
    is_row_count_expectation,
)
from backend.app.db.models import ORCHESTRATION_PROVIDERS, Check, Connection, Share, Suite
from backend.app.services import audit_service, run_target
from backend.app.services.asset_service import resolve_and_upsert_asset
from backend.app.services.column_classification import is_sensitive

log = get_logger(__name__)


def accessible_suite_ids(
    user_id: uuid.UUID, *, include_all: bool = False
) -> Select[tuple[uuid.UUID]]:
    """Subquery of suite ids the user can access — owned (`created_by`) or shared."""
    if include_all:
        return select(Suite.id)
    shared = select(Share.suite_id).where(Share.user_id == user_id)
    return select(Suite.id).where(or_(Suite.created_by == user_id, Suite.id.in_(shared)))


class SuiteNotFoundError(DataQError):
    status_code = 404
    code = "suite_not_found"


class SuiteConnectionInvalidError(DataQError):
    status_code = 422
    code = "suite_connection_invalid"


class ColumnPolicyInvalidError(DataQError):
    status_code = 422
    code = "column_policy_invalid"


def create_suite(
    session: Session,
    *,
    name: str,
    description: str | None,
    connection_id: uuid.UUID,
    created_by: uuid.UUID,
    target: dict[str, Any] | None = None,
) -> Suite:
    """Create a suite bound to an existing connection."""
    connection = session.get(Connection, connection_id)
    if connection is None:
        raise SuiteConnectionInvalidError(
            "connection not found", detail={"connection_id": str(connection_id)}
        )
    if connection.type in ORCHESTRATION_PROVIDERS:
        # ADF/Airflow are orchestration providers, never suite datasources (CLAUDE.md §4): a suite's
        # connection is where its checks run.
        raise SuiteConnectionInvalidError(
            "orchestration providers cannot be a suite's datasource; "
            "they trigger suites via trigger bindings",
            detail={"connection_id": str(connection_id), "type": connection.type},
        )
    if target is not None:
        run_target.validate_target(connection.type, target)
    # Resolve the target to a first-class asset (ADR 0034). Fail-soft — a NULL
    # asset_id never blocks the save (resolve_and_upsert_asset never raises).
    asset_id = resolve_and_upsert_asset(session, connection, target)
    suite = Suite(
        name=name,
        description=description,
        connection_id=connection_id,
        created_by=created_by,
        target=target,
        asset_id=asset_id,
    )
    session.add(suite)
    audit_service.record_entity_change(
        session,
        action="suite.create",
        entity_type="suite",
        entity=suite,
        actor=created_by,
    )
    session.commit()
    session.refresh(suite)
    log.info("suite_created", suite_id=str(suite.id), connection_id=str(connection_id))
    return suite


def list_suites(
    session: Session,
    *,
    user_id: uuid.UUID,
    connection_id: uuid.UUID | None = None,
    include_all: bool = False,
) -> list[Suite]:
    """Suites the user can access: owned (`created_by`) or shared with them — or
    *all* suites when `include_all` (the workspace-admin view, ADR 0027).
    """
    stmt = (
        select(Suite)
        .where(Suite.id.in_(accessible_suite_ids(user_id, include_all=include_all)))
        .order_by(Suite.created_at.desc())
    )
    if connection_id is not None:
        stmt = stmt.where(Suite.connection_id == connection_id)
    return list(session.scalars(stmt))


def get_suite(session: Session, suite_id: uuid.UUID) -> Suite:
    suite = session.get(Suite, suite_id)
    if suite is None:
        raise SuiteNotFoundError("suite not found", detail={"suite_id": str(suite_id)})
    return suite


def _reject_sampling_with_row_count_checks(
    session: Session, suite_id: uuid.UUID, target: dict[str, Any], conn_type: str
) -> None:
    """422 if this target turns on sampling under existing row-count checks (#595 C6)."""
    if run_target.resolve_target(conn_type, target).sampling is None:
        return
    # Names are read back so the message can point at the actual obstacle — "one
    # of your checks conflicts" sends an author hunting through a long suite.
    blocked = sorted(
        name
        for name, expectation_type in session.execute(
            select(Check.name, Check.expectation_type).where(Check.suite_id == suite_id)
        ).all()
        if is_row_count_expectation(expectation_type)
    )
    if blocked:
        raise run_target.SuiteTargetInvalidError(
            f"{SAMPLING_ROW_COUNT_CONFLICT} Conflicting checks: {', '.join(blocked)}.",
            detail={"checks": blocked},
        )


def update_suite(
    session: Session,
    suite_id: uuid.UUID,
    *,
    name: str | None = None,
    description: str | None = None,
    target: dict[str, Any] | None = None,
    actor_id: uuid.UUID | None = None,
) -> Suite:
    """Partial update of name / description / target. `connection_id` is immutable."""
    suite = get_suite(session, suite_id)
    # Before ANY field is mutated — a snapshot taken later would record the new
    # state as the old one, which is worse than no `before` at all.
    audit_before = audit_service.snapshot("suite", suite)
    if name is not None:
        suite.name = name
    if description is not None:
        suite.description = description
    if target is not None:
        connection = session.get(Connection, suite.connection_id)
        assert connection is not None  # FK is RESTRICT; a suite always has its connection
        run_target.validate_target(connection.type, target)
        _reject_sampling_with_row_count_checks(session, suite_id, target, connection.type)
        # Skip the re-resolve + upsert on a no-op PATCH (target re-sent unchanged and
        # already linked) — the identity hasn't moved, so it's a wasted DB write.
        is_noop = target == suite.target and suite.asset_id is not None
        suite.target = target
        if not is_noop:
            # Re-point the suite at the asset its new target resolves to (ADR 0034).
            # Fail-soft: an unresolvable target leaves asset_id NULL, never 500s.
            suite.asset_id = resolve_and_upsert_asset(session, connection, target)
    audit_service.record_entity_change(
        session,
        action="suite.update",
        entity_type="suite",
        entity=suite,
        actor=actor_id,
        before=audit_before,
    )
    session.commit()
    session.refresh(suite)
    log.info("suite_updated", suite_id=str(suite.id))
    return suite


def set_column_policy(
    session: Session,
    suite_id: uuid.UUID,
    *,
    identifier_column: str | None,
    pii_columns: list[str],
    require_classification: bool | None = None,
    actor_id: uuid.UUID | None = None,
    machine_write: bool = False,
) -> Suite:
    """Set the suite's failing-sample redaction policy (#415): the shown
    ``identifier_column`` (a non-PII row locator) + the always-masked ``pii_columns``.
    """
    suite = get_suite(session, suite_id)
    audit_before = audit_service.snapshot("suite", suite)
    #: The stored policy, read before it is replaced — the source for any field
    #: this call left unspecified (see the tri-state note below).
    suite_before = suite.column_policy
    pii = [c for c in dict.fromkeys(pii_columns) if c]  # de-dupe, drop blanks, keep order
    if identifier_column and identifier_column in pii:
        raise ColumnPolicyInvalidError(
            "identifier_column cannot also be a PII column",
            detail={"identifier_column": identifier_column},
        )
    # A shown locator must be non-PII: reject a name that classifies as direct PII (email /
    # account_number / tax_id …).
    if identifier_column and is_sensitive(identifier_column):
        raise ColumnPolicyInvalidError(
            "identifier_column looks like PII — pick a non-PII locator (e.g. an order id)",
            detail={"identifier_column": identifier_column},
        )
    policy: dict[str, Any] = {"pii_columns": pii}
    if identifier_column:
        policy["identifier_column"] = identifier_column
    # Tri-state, and it is a security decision rather than a style one.
    keep = (
        require_classification
        if require_classification is not None
        else bool((suite_before or {}).get("require_classification"))
    )
    if keep:
        policy["require_classification"] = True
    suite.column_policy = policy
    # Among the highest-value events in the table: this changes WHAT PERSONAL DATA the product will
    # surface in a failing-row sample.
    if not machine_write:
        audit_service.record_entity_change(
            session,
            action="suite.column_policy_update",
            entity_type="suite",
            entity=suite,
            actor=actor_id,
            before=audit_before,
        )
    session.commit()
    session.refresh(suite)
    log.info("suite_column_policy_set", suite_id=str(suite.id))
    return suite


def delete_suite(
    session: Session, suite_id: uuid.UUID, *, actor_id: uuid.UUID | None = None
) -> None:
    """Delete a suite; its checks cascade (Suite.checks delete-orphan + FK)."""
    suite = get_suite(session, suite_id)
    audit_before = audit_service.snapshot("suite", suite)
    session.delete(suite)
    audit_service.record_entity_change(
        session,
        action="suite.delete",
        entity_type="suite",
        entity=None,
        actor=actor_id,
        before=audit_before,
    )
    session.commit()
    log.info("suite_deleted", suite_id=str(suite_id))
