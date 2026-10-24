"""Global search behind the command palette (⌘K): suites, checks, connections and runs by name.

Every group is scoped the way its own list endpoint is — suites by the caller's grants (owned or
shared, every suite for a workspace admin), checks and runs only through a visible suite,
connections unscoped like `GET /connections`. A suite the caller cannot see contributes nothing,
not even its name on a check or run hit, so the result set never proves an unshared suite exists.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Literal

from sqlalchemy import ColumnElement, Row, Select, String, case, cast, func, or_, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from backend.app.core.errors import DataQError
from backend.app.db.models import Check, Connection, Run, Suite
from backend.app.services.suite_authz import effective_permissions
from backend.app.services.suite_service import accessible_suite_filter, accessible_suite_ids
from backend.app.services.user_service import _escape_like as escape_like

GroupName = Literal["suites", "checks", "connections", "runs"]
GROUPS: Final[tuple[GroupName, ...]] = ("suites", "checks", "connections", "runs")

MAX_QUERY_LEN: Final = 200
DEFAULT_LIMIT: Final = 8
MAX_LIMIT: Final = 20
# A run id prefix only counts as a query once it is long enough not to match most of the table.
_RUN_ID_PREFIX_MIN: Final = 4
_SUITE_EDIT_LEVELS: Final = frozenset({"edit", "admin", "owner"})


class SearchQueryInvalidError(DataQError):
    status_code = 422
    code = "search_query_invalid"


@dataclass(frozen=True)
class SearchHit:
    id: uuid.UUID
    label: str
    sublabel: str | None
    href: str
    when: datetime | None = None


@dataclass
class SearchResults:
    query: str
    suites: list[SearchHit] = field(default_factory=list)
    checks: list[SearchHit] = field(default_factory=list)
    connections: list[SearchHit] = field(default_factory=list)
    runs: list[SearchHit] = field(default_factory=list)
    truncated_groups: list[GroupName] = field(default_factory=list)


def normalise_query(raw: str) -> str:
    """Strip and bound the query; an empty or over-long one is a 422, never an empty 200."""
    term = raw.strip()
    if not term:
        raise SearchQueryInvalidError("search query must not be blank")
    if len(term) > MAX_QUERY_LEN:
        raise SearchQueryInvalidError(
            f"search query is longer than {MAX_QUERY_LEN} characters",
            detail={"max_length": MAX_QUERY_LEN},
        )
    return term


def _rank(column: InstrumentedAttribute[str], term: str) -> ColumnElement[int]:
    return case((column.ilike(f"{escape_like(term)}%", escape="\\"), 0), else_=1)


def _page(
    session: Session, stmt: Select[Any], count_stmt: Select[tuple[int]], limit: int
) -> tuple[list[Row[Any]], bool]:
    """The first `limit` rows and whether more exist — from the real count, not `len == limit`."""
    total = session.scalar(count_stmt) or 0
    rows = list(session.execute(stmt.limit(limit)).all())
    return rows, total > limit


def search(
    session: Session,
    *,
    user_id: uuid.UUID,
    query: str,
    limit: int = DEFAULT_LIMIT,
    include_all: bool = False,
    is_admin: bool = False,
) -> SearchResults:
    term = normalise_query(query)
    pattern = f"%{escape_like(term)}%"
    visible = accessible_suite_ids(user_id, include_all=include_all)
    results = SearchResults(query=term)

    # Suites — the caller's own population, exactly `GET /suites`.
    suite_where = (Suite.id.in_(visible), Suite.name.ilike(pattern, escape="\\"))
    suite_rows, more = _page(
        session,
        select(Suite).where(*suite_where).order_by(_rank(Suite.name, term), Suite.name, Suite.id),
        select(func.count()).select_from(Suite).where(*suite_where),
        limit,
    )
    suites = [row[0] for row in suite_rows]
    results.suites = [
        SearchHit(id=s.id, label=s.name, sublabel=None, href=f"/suites/{s.id}") for s in suites
    ]
    if more:
        results.truncated_groups.append("suites")

    # Checks — reachable only through a visible suite; the suite name is the sublabel.
    check_where = (
        accessible_suite_filter(Check.suite_id, user_id, include_all=include_all),
        Check.name.ilike(pattern, escape="\\"),
    )
    check_rows, more = _page(
        session,
        select(Check, Suite.name)
        .join(Suite, Suite.id == Check.suite_id)
        .where(*check_where)
        .order_by(_rank(Check.name, term), Check.name, Check.id),
        select(func.count()).select_from(Check).where(*check_where),
        limit,
    )
    check_suites = {row[0].suite_id for row in check_rows}
    if check_suites:
        suite_objs = list(session.scalars(select(Suite).where(Suite.id.in_(check_suites))))
        levels = effective_permissions(session, suite_objs, user_id)
    else:
        levels = {}
    results.checks = [
        SearchHit(
            id=check.id,
            label=check.name,
            sublabel=suite_name,
            href=(
                f"/suites/{check.suite_id}/checks/{check.id}/edit"
                if levels.get(check.suite_id) in _SUITE_EDIT_LEVELS
                else f"/suites/{check.suite_id}"
            ),
        )
        for check, suite_name in check_rows
    ]
    if more:
        results.truncated_groups.append("checks")

    # Connections — unscoped, like `GET /connections`; only an Admin has a page per connection.
    conn_where = (Connection.name.ilike(pattern, escape="\\"),)
    conn_rows, more = _page(
        session,
        select(Connection)
        .where(*conn_where)
        .order_by(_rank(Connection.name, term), Connection.name, Connection.id),
        select(func.count()).select_from(Connection).where(*conn_where),
        limit,
    )
    results.connections = [
        SearchHit(
            id=c.id,
            label=c.name,
            sublabel=f"{c.type} · {c.env}",
            href=f"/connections/{c.id}/edit" if is_admin else "/connections",
        )
        for (c,) in conn_rows
    ]
    if more:
        results.truncated_groups.append("connections")

    # Runs — matched by their suite's name (and by id prefix), newest first.
    run_match: list[ColumnElement[bool]] = [Suite.name.ilike(pattern, escape="\\")]
    if len(term) >= _RUN_ID_PREFIX_MIN and _looks_like_uuid_prefix(term):
        run_match.append(cast(Run.id, String).like(f"{term.lower()}%"))
    run_where = (
        accessible_suite_filter(Run.suite_id, user_id, include_all=include_all),
        or_(*run_match),
    )
    run_rows, more = _page(
        session,
        select(Run, Suite.name)
        .join(Suite, Suite.id == Run.suite_id)
        .where(*run_where)
        .order_by(Run.created_at.desc(), Run.id.desc()),
        select(func.count())
        .select_from(Run)
        .join(Suite, Suite.id == Run.suite_id)
        .where(*run_where),
        limit,
    )
    results.runs = [
        SearchHit(
            id=run.id,
            label=suite_name,
            sublabel=run.status,
            when=run.started_at or run.created_at,
            href=f"/results/{run.id}",
        )
        for run, suite_name in run_rows
    ]
    if more:
        results.truncated_groups.append("runs")
    return results


def _looks_like_uuid_prefix(term: str) -> bool:
    return all(ch in "0123456789abcdefABCDEF-" for ch in term)
