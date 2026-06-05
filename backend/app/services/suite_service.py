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

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.db.models import Connection, Share, Suite

log = get_logger(__name__)


class SuiteNotFoundError(DataQError):
    status_code = 404
    code = "suite_not_found"


class SuiteConnectionInvalidError(DataQError):
    status_code = 422
    code = "suite_connection_invalid"


def create_suite(
    session: Session,
    *,
    name: str,
    description: str | None,
    connection_id: uuid.UUID,
    created_by: uuid.UUID,
) -> Suite:
    """Create a suite bound to an existing connection.

    Raises `SuiteConnectionInvalidError` (422) if the connection does not exist
    — caught here so a bad `connection_id` is a clean validation error, not a
    raw FK IntegrityError surfacing as 500.
    """
    if session.get(Connection, connection_id) is None:
        raise SuiteConnectionInvalidError(
            "connection not found", detail={"connection_id": str(connection_id)}
        )
    suite = Suite(
        name=name,
        description=description,
        connection_id=connection_id,
        created_by=created_by,
    )
    session.add(suite)
    session.commit()
    session.refresh(suite)
    log.info("suite_created", suite_id=str(suite.id), connection_id=str(connection_id))
    return suite


def list_suites(
    session: Session, *, user_id: uuid.UUID, connection_id: uuid.UUID | None = None
) -> list[Suite]:
    """Suites the user can access: owned (`created_by`) or shared with them."""
    shared = select(Share.suite_id).where(Share.user_id == user_id)
    stmt = (
        select(Suite)
        .where(or_(Suite.created_by == user_id, Suite.id.in_(shared)))
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
) -> Suite:
    """Partial update of name / description. `connection_id` is immutable."""
    suite = get_suite(session, suite_id)
    if name is not None:
        suite.name = name
    if description is not None:
        suite.description = description
    session.commit()
    session.refresh(suite)
    log.info("suite_updated", suite_id=str(suite.id))
    return suite


def delete_suite(session: Session, suite_id: uuid.UUID) -> None:
    """Delete a suite; its checks cascade (Suite.checks delete-orphan + FK)."""
    suite = get_suite(session, suite_id)
    session.delete(suite)
    session.commit()
    log.info("suite_deleted", suite_id=str(suite_id))
