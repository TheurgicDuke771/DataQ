"""Suite CRUD — datasource-type-agnostic, FastAPI-free.

A suite is a named collection of checks bound to exactly one connection
(CLAUDE.md §10). This layer validates the connection exists on create, then
treats `connection_id` as immutable — re-pointing a suite at a different
connection would silently invalidate every child check's table/column semantics,
so it is not an update path.

Like `connection_service` / `run_service`: takes a `Session`, returns ORM
models, raises `DataQError` subclasses; the API layer owns request/response
shapes and dependency wiring. Share-based access control is layered on separately
(the Week-3 suite-sharing task); this service is authenticated CRUD only.
"""

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
    """Subquery of suite ids the user can access — owned (`created_by`) or shared.

    The single source of truth for suite visibility, shared by `list_suites` and
    the run/result reads (`run_service.list_runs`, `dashboard_service`) so the
    owned-OR-shared rule is encoded once — a divergence here would be a silent
    authz leak.

    `include_all=True` returns *every* suite id — the workspace-admin view (ADR
    0027): a workspace-admin is an implicit admin on every suite, so their lists /
    dashboard / results span the whole workspace, not just owned-or-shared. The
    caller resolves admin status at the API layer (`is_workspace_admin`) and only
    a workspace-admin may pass it.
    """
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
    """Create a suite bound to an existing connection.

    Raises `SuiteConnectionInvalidError` (422) if the connection does not exist
    — caught here so a bad `connection_id` is a clean validation error, not a
    raw FK IntegrityError surfacing as 500. A provided ``target`` is validated
    against the connection's datasource type (422 if malformed); a suite may also
    be created targetless (NULL) and have a target set later via update.
    """
    connection = session.get(Connection, connection_id)
    if connection is None:
        raise SuiteConnectionInvalidError(
            "connection not found", detail={"connection_id": str(connection_id)}
        )
    if connection.type in ORCHESTRATION_PROVIDERS:
        # ADF/Airflow are orchestration providers, never suite datasources
        # (CLAUDE.md §4): a suite's connection is where its checks run. They
        # relate to suites only via trigger_bindings (trigger on pipeline success).
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
    *all* suites when `include_all` (the workspace-admin view, ADR 0027)."""
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
    """422 if this target turns on sampling under existing row-count checks (#595 C6).

    The other half of the gate. `check_service` refuses a row-count expectation on
    a suite that already samples; this refuses sampling on a suite that already has
    one — otherwise the combination is reachable simply by doing it in the other
    order, and the check would start silently measuring the sample instead of the
    dataset while still reading as a healthy configuration.

    Deliberately raised as `SuiteTargetInvalidError`, the same 422 every other
    target-shape complaint uses, so the run-target editor surfaces it where the
    author made the change.
    """
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
    """Partial update of name / description / target. `connection_id` is immutable.

    A provided ``target`` is validated against the suite's connection type (422
    if malformed) and replaces the existing target. ``None`` means "leave the
    target unchanged" (the same partial-update semantics as name/description), so
    this path sets/replaces a target but never clears one back to NULL.
    """
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

    The identifier must not also be listed PII (that would mask the very column meant
    to locate the row) — a 422. Stored as ``{"identifier_column"?, "pii_columns"}``;
    the ``identifier_column`` key is omitted when ``None`` (no locator chosen). The
    datasource-tag governance floor still overrules for masking at redaction time.
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
    # A shown locator must be non-PII: reject a name that classifies as direct PII
    # (email / account_number / tax_id …). The redaction path also floors this, but a
    # 422 here gives immediate feedback rather than a silently-masked "identifier".
    if identifier_column and is_sensitive(identifier_column):
        raise ColumnPolicyInvalidError(
            "identifier_column looks like PII — pick a non-PII locator (e.g. an order id)",
            detail={"identifier_column": identifier_column},
        )
    policy: dict[str, Any] = {"pii_columns": pii}
    if identifier_column:
        policy["identifier_column"] = identifier_column
    # Tri-state, and it is a security decision rather than a style one. `None`
    # means "leave as it was": this endpoint is a full replacement, and every
    # client that predates the flag — the shipped frontend's Save, the MCP
    # `set_column_policy` tool — sends the policy without it. Defaulting to
    # `False` would let any of them silently switch fail-closed OFF while
    # editing an unrelated field, which is the worst failure available to a
    # control whose entire job is to be conservative.
    #
    # Turning it off stays possible; it just has to be said out loud.
    keep = (
        require_classification
        if require_classification is not None
        else bool((suite_before or {}).get("require_classification"))
    )
    if keep:
        policy["require_classification"] = True
    suite.column_policy = policy
    # Among the highest-value events in the table: this changes WHAT PERSONAL DATA
    # the product will surface in a failing-row sample. `before`/`after` carry both
    # policies, so "why did this column start appearing?" is answerable.
    # `machine_write` is the auto-classify beat task (#634), which derives a policy
    # for a suite that has none. ADR 0041 §2.1 excludes machine writes from the
    # audit log, and there is deliberately NO `system` actor_kind — so this path
    # must record nothing rather than record an unattributable event.
    #
    # An explicit flag rather than "no actor_id ⇒ no event": the absence of an
    # actor is exactly what a forgotten `actor_id=` at a real call site looks
    # like, so inferring the exclusion from it would silently drop a principal's
    # act. Here the caller has to say so, and a test pins that the beat path
    # writes nothing.
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
    """Delete a suite; its checks cascade (Suite.checks delete-orphan + FK).

    The most destructive act in the product: the cascade takes every check, run
    and result the suite ever produced (#540), and ADR 0041 §2.3 rejected
    soft-delete rather than make it recoverable. What it gets instead is this
    event — WHAT was destroyed, BY WHOM, WHEN — which is honesty about an
    irreversible action, not undo, and the ADR does not pretend otherwise.
    """
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
