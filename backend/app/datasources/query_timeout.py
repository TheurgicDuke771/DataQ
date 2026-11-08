"""The one wall-clock limit on a query DataQ sends a SQL datasource (#2496).

Each generic SQL engine applies `query_timeout_s` through its own mechanism where it builds a
session (its `connect_args`, or `SqlEngineSpec.query_timeout_setup` once connected). Snowflake and
Unity Catalog sessions are also built inside GX, so they are limited by `register_session_limit`
on every new connection instead (#2607). A query that hits the limit fails with the engine's own
error, which `is_query_timeout` recognises so the run reports `query_timeout_message` instead of
an opaque driver error.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from backend.app.core.errors import SafeMonitorError
from backend.app.core.logging import get_logger

log = get_logger(__name__)

SETTING = "DATASOURCE_QUERY_TIMEOUT_S"

# Lower-cased fragments of each engine's own "statement ran out of time" error. Specific on
# purpose: a connect or network timeout is a different failure with a different fix.
_MARKERS: tuple[str, ...] = (
    # PostgreSQL / Redshift: SQLSTATE 57014 raised by `statement_timeout`.
    "canceling statement due to statement timeout",
    # MySQL 3024 (`max_execution_time`).
    "maximum statement execution time exceeded",
    # MariaDB 1969 (`max_statement_time`).
    "max_statement_time exceeded",
    # Trino EXCEEDED_TIME_LIMIT (`query_max_execution_time`).
    "exceeded_time_limit",
    "exceeded the maximum execution time limit",
    # SQL Server over pyodbc: HYT00. python-tds raises a bare `TimeoutError: timed out`, which
    # its own login timeout also raises, so that one stays a connectivity failure.
    "query timeout expired",
    # BigQuery (`job_timeout_ms`).
    "job timed out after",
    # Snowflake 000630 (`STATEMENT_TIMEOUT_IN_SECONDS`, session or warehouse).
    "reached its statement or warehouse timeout",
    # Databricks SQL QUERY_EXECUTION_TIMEOUT_EXCEEDED (`STATEMENT_TIMEOUT`).
    "query_execution_timeout_exceeded",
    "query execution was cancelled due to exceeding the timeout",
    # DataQ-raised: the Athena poll limit.
    SETTING.lower(),
)


class QueryTimeoutError(SafeMonitorError, RuntimeError):
    """A query exceeded `DATASOURCE_QUERY_TIMEOUT_S`; the message is safe to show verbatim."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or query_timeout_message())


def query_timeout_s() -> int:
    """The configured limit, in whole seconds (always positive)."""
    from backend.app.core.config import get_settings

    return int(get_settings().datasource_query_timeout_s)


def query_timeout_message() -> str:
    """The fixed, secret-free reason a query that hit the limit reports."""
    return (
        f"A query ran longer than the datasource query limit ({SETTING} = "
        f"{query_timeout_s()} seconds), or a lower limit the datasource itself sets, and was "
        f"cancelled. Narrow the check or the run target, or raise {SETTING} if the query "
        "legitimately needs longer."
    )


def is_query_timeout_message(message: str | None) -> bool:
    """Whether ``message`` is an engine's (or DataQ's) query-limit error."""
    if not message:
        return False
    haystack = message.lower()
    return any(marker in haystack for marker in _MARKERS)


def is_query_timeout(exc: BaseException) -> bool:
    """Whether ``exc``, or anything in its cause/context chain, is a query-limit error."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, QueryTimeoutError) or is_query_timeout_message(
            f"{type(current).__name__}: {current}"
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


SessionLimitSetup = Callable[[Any, int], None]

# Engine name -> the DBAPI module prefix it matches; one listener each, however often registered.
_REGISTERED: dict[str, str] = {}


def apply_session_limit(dbapi_connection: Any, setup: SessionLimitSetup, *, engine: str) -> None:
    """Run ``setup`` on a fresh connection. A failure leaves the session without the limit and is
    logged rather than raised: these engines cannot be exercised here (#2224), and a statement
    one warehouse rejects must not fail every run on it.
    """
    try:
        setup(dbapi_connection, query_timeout_s())
    except Exception as exc:
        log.warning("query_limit_not_applied", engine=engine, error_type=type(exc).__name__)


def register_session_limit(engine: str, dbapi_module: str, setup: SessionLimitSetup) -> None:
    """Apply the query limit to every new DBAPI connection whose class lives under
    ``dbapi_module``, whoever built the SQLAlchemy engine (GX builds its own). Idempotent.
    """
    if engine in _REGISTERED:
        return
    from sqlalchemy import event
    from sqlalchemy.pool import Pool

    prefix = dbapi_module + "."

    def _on_connect(dbapi_connection: Any, _record: Any) -> None:
        if type(dbapi_connection).__module__.startswith(prefix):
            apply_session_limit(dbapi_connection, setup, engine=engine)

    event.listen(Pool, "connect", _on_connect)
    _REGISTERED[engine] = dbapi_module
