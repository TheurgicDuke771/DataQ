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

from sqlalchemy import ColumnElement, Select, case, or_, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from backend.app.core.errors import DataQError
from backend.app.db.models import Check, Connection, Run, Suite
from backend.app.services.like_match import contains_ci, starts_with_ci
from backend.app.services.suite_authz import effective_permissions
from backend.app.services.suite_service import (
    accessible_suite_filter,
    accessible_suite_ids,
)

GroupName = Literal["suites", "checks", "connections", "runs"]
GROUPS: Final[tuple[GroupName, ...]] = ("suites", "checks", "connections", "runs")

MAX_QUERY_LEN: Final = 200
DEFAULT_LIMIT: Final = 8
MAX_LIMIT: Final = 20
# A run-id prefix counts once it covers the first UUID group: shorter hex runs (`cafe`, `2024`,
# `beef`) are ordinary words and numbers, and would pull in unrelated runs.
RUN_ID_PREFIX_MIN_HEX: Final = 8
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


def _prefix_first(column: InstrumentedAttribute[str], term: str) -> ColumnElement[int]:
    return case((starts_with_ci(column, term), 0), else_=1)


def _page(session: Session, stmt: Select[Any], limit: int) -> tuple[list[Any], bool]:
    """The first `limit` rows and whether more exist — one row past the limit is fetched so the
    answer is exact without a second COUNT query."""
    rows = list(session.execute(stmt.limit(limit + 1)).all())
    return rows[:limit], len(rows) > limit


def run_id_range(term: str) -> tuple[uuid.UUID, uuid.UUID] | None:
    """The `[lo, hi]` UUID range a hex prefix denotes, or None when `term` is not one.

    Hyphens are ignored so a pasted `f105e319-5912` works; the range lets the lookup use the
    primary key instead of casting every row's id to text.
    """
    hex_part = term.replace("-", "").lower()
    if len(hex_part) < RUN_ID_PREFIX_MIN_HEX or len(hex_part) > 32:
        return None
    if any(ch not in "0123456789abcdef" for ch in hex_part):
        return None
    pad = 32 - len(hex_part)
    return uuid.UUID(hex_part + "0" * pad), uuid.UUID(hex_part + "f" * pad)


def search(
    session: Session,
    *,
    user_id: uuid.UUID,
    query: str,
    limit: int = DEFAULT_LIMIT,
    is_admin: bool = False,
) -> SearchResults:
    """`is_admin` (the caller's workspace role) widens the suite population to every suite and
    gives connections their own page, the two things ADR 0033 grants an Admin here."""
    term = normalise_query(query)
    visible = accessible_suite_ids(user_id, include_all=is_admin)
    results = SearchResults(query=term)

    # Suites — the caller's own population, exactly `GET /suites`.
    suite_rows, more = _page(
        session,
        select(Suite)
        .where(Suite.id.in_(visible), contains_ci(Suite.name, term))
        .order_by(_prefix_first(Suite.name, term), Suite.name, Suite.id),
        limit,
    )
    results.suites = [
        SearchHit(id=s.id, label=s.name, sublabel=None, href=f"/suites/{s.id}")
        for (s,) in suite_rows
    ]
    if more:
        results.truncated_groups.append("suites")

    # Checks — reachable only through a visible suite; the suite name is the sublabel and its
    # permission level decides whether the hit opens the editor or the suite.
    check_rows, more = _page(
        session,
        select(Check, Suite)
        .join(Suite, Suite.id == Check.suite_id)
        .where(
            accessible_suite_filter(Check.suite_id, user_id, include_all=is_admin),
            contains_ci(Check.name, term),
        )
        .order_by(_prefix_first(Check.name, term), Check.name, Check.id),
        limit,
    )
    check_suites = {suite.id: suite for _, suite in check_rows}
    levels = (
        effective_permissions(session, list(check_suites.values()), user_id) if check_suites else {}
    )
    results.checks = [
        SearchHit(
            id=check.id,
            label=check.name,
            sublabel=suite.name,
            href=(
                f"/suites/{suite.id}/checks/{check.id}/edit"
                if levels.get(suite.id) in _SUITE_EDIT_LEVELS
                else f"/suites/{suite.id}"
            ),
        )
        for check, suite in check_rows
    ]
    if more:
        results.truncated_groups.append("checks")

    # Connections — unscoped, like `GET /connections`; only an Admin has a page per connection.
    conn_rows, more = _page(
        session,
        select(Connection)
        .where(contains_ci(Connection.name, term))
        .order_by(_prefix_first(Connection.name, term), Connection.name, Connection.id),
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

    # Runs — those of a suite whose name matches (an indexable `suite_id IN (...)`), plus a run
    # whose id starts with the term (a primary-key range); newest first.
    matching_suites = select(Suite.id).where(Suite.id.in_(visible), contains_ci(Suite.name, term))
    run_match: list[ColumnElement[bool]] = [Run.suite_id.in_(matching_suites)]
    id_range = run_id_range(term)
    if id_range is not None:
        run_match.append(Run.id.between(*id_range))
    run_rows, more = _page(
        session,
        select(Run, Suite.name)
        .join(Suite, Suite.id == Run.suite_id)
        .where(
            accessible_suite_filter(Run.suite_id, user_id, include_all=is_admin),
            or_(*run_match),
        )
        .order_by(Run.created_at.desc(), Run.id.desc()),
        limit,
    )
    results.runs = [
        SearchHit(
            id=run.id,
            label=suite_name,
            sublabel=_run_sublabel(run, id_range),
            href=f"/results/{run.id}",
            when=run.started_at or run.created_at,
        )
        for run, suite_name in run_rows
    ]
    if more:
        results.truncated_groups.append("runs")
    return results


def _run_sublabel(run: Run, id_range: tuple[uuid.UUID, uuid.UUID] | None) -> str:
    # A run that matched on its id says so — its suite's name alone would not explain the hit.
    if id_range is not None and id_range[0] <= run.id <= id_range[1]:
        return f"{run.status} · run {str(run.id)[:8]}…"
    return run.status
