"""Shared SQL-datasource primitives (#428).

The single source of truth for two things every SQL-speaking path needs and
previously copied per-module:

* :data:`SQL_IDENTIFIER_RE` / :func:`is_sql_identifier` — the plain-identifier
  allowlist (the Snowflake/Databricks unquoted-identifier set: letter/underscore
  lead, then word chars or ``$``). SQL identifiers have no bind-parameter slot,
  so every path that interpolates a user-supplied table/schema/column into a
  query string (monitor config, the profiler's query builders) must validate
  against this exact set first. One definition means widening it (e.g. quoted /
  unicode identifiers) happens everywhere at once, never in one copy silently.
  Callers keep their own error types (a monitor raises ``MonitorConfigError``,
  the profiler a 422 ``ProfileIdentifierInvalidError``) — the shared piece is
  the *decision*, not the failure shape.

* :func:`run_monitors_over_engine` — the one engine → connection → scalar loop
  the SQL runners (Snowflake / Unity Catalog) execute monitor checks through.
  The runners differ only in how they build their URL/engine; the lifecycle
  (open one connection, feed ``evaluate_monitors`` a scalar fetcher, let a
  connection failure propagate to fail the run) is identical and lives here.
  The caller owns the engine (build + dispose) — that seam is what lets #427
  later thread a per-run shared engine through without touching this helper.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from backend.app.datasources.base import CheckOutcome, MonitorSpec

SQL_IDENTIFIER_RE: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def is_sql_identifier(name: object) -> bool:
    """True when ``name`` is a plain SQL identifier safe to interpolate.

    Anything else (spaces, quotes, dots, non-strings) is refused — it can't be
    made injection-safe by quoting alone at the call sites that use this.
    """
    return isinstance(name, str) and bool(SQL_IDENTIFIER_RE.match(name))


def run_monitors_over_engine(
    engine: Engine,
    *,
    table: str,
    schema: str | None,
    catalog: str | None,
    monitors: list[MonitorSpec],
) -> list[CheckOutcome]:
    """Run monitor checks over ONE connection from ``engine``, one outcome each.

    Opens a single connection and sources every monitor's scalar from it via
    ``evaluate_monitors`` (a bad monitor errors only itself; a connection-level
    failure propagates and fails the whole run — the open happens before the
    per-monitor loop). The engine's lifecycle belongs to the caller.
    """
    # Lazy imports: sqlalchemy is heavy (runner-module convention), and monitors.py
    # imports this module for the identifier allowlist — importing it back at
    # module scope would be a cycle.
    from sqlalchemy import text

    from backend.app.datasources.monitors import evaluate_monitors

    with engine.connect() as conn:
        return evaluate_monitors(
            lambda sql: conn.execute(text(sql)).scalar(),
            table=table,
            schema=schema,
            catalog=catalog,
            monitors=monitors,
        )
