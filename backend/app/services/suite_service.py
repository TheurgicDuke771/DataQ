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
from backend.app.db.models import ORCHESTRATION_PROVIDERS, Connection, Share, Suite
from backend.app.services import run_target

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
    suite = Suite(
        name=name,
        description=description,
        connection_id=connection_id,
        created_by=created_by,
        target=target,
    )
    session.add(suite)
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


def update_suite(
    session: Session,
    suite_id: uuid.UUID,
    *,
    name: str | None = None,
    description: str | None = None,
    target: dict[str, Any] | None = None,
) -> Suite:
    """Partial update of name / description / target. `connection_id` is immutable.

    A provided ``target`` is validated against the suite's connection type (422
    if malformed) and replaces the existing target. ``None`` means "leave the
    target unchanged" (the same partial-update semantics as name/description), so
    this path sets/replaces a target but never clears one back to NULL.
    """
    suite = get_suite(session, suite_id)
    if name is not None:
        suite.name = name
    if description is not None:
        suite.description = description
    if target is not None:
        connection = session.get(Connection, suite.connection_id)
        assert connection is not None  # FK is RESTRICT; a suite always has its connection
        run_target.validate_target(connection.type, target)
        suite.target = target
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
) -> Suite:
    """Set the suite's failing-sample redaction policy (#415): the shown
    ``identifier_column`` (a non-PII row locator) + the always-masked ``pii_columns``.

    The identifier must not also be listed PII (that would mask the very column meant
    to locate the row) — a 422. Stored as ``{"identifier_column"?, "pii_columns"}``;
    the ``identifier_column`` key is omitted when ``None`` (no locator chosen). The
    datasource-tag governance floor still overrules for masking at redaction time.
    """
    pii = [c for c in dict.fromkeys(pii_columns) if c]  # de-dupe, drop blanks, keep order
    if identifier_column and identifier_column in pii:
        raise ColumnPolicyInvalidError(
            "identifier_column cannot also be a PII column",
            detail={"identifier_column": identifier_column},
        )
    policy: dict[str, Any] = {"pii_columns": pii}
    if identifier_column:
        policy["identifier_column"] = identifier_column
    suite = get_suite(session, suite_id)
    suite.column_policy = policy
    session.commit()
    session.refresh(suite)
    log.info("suite_column_policy_set", suite_id=str(suite.id))
    return suite


def delete_suite(session: Session, suite_id: uuid.UUID) -> None:
    """Delete a suite; its checks cascade (Suite.checks delete-orphan + FK)."""
    suite = get_suite(session, suite_id)
    session.delete(suite)
    session.commit()
    log.info("suite_deleted", suite_id=str(suite_id))
