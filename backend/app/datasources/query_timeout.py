"""The one wall-clock limit on a query DataQ sends a SQL datasource (#2496).

Each generic SQL engine applies `query_timeout_s` through its own mechanism where it builds a
session (its `connect_args`, or `SqlEngineSpec.query_timeout_setup` once connected). A query that
hits it fails with the engine's own error, which `is_query_timeout` recognises so the run reports
`query_timeout_message` instead of an opaque driver error.
"""

from __future__ import annotations

from backend.app.core.errors import SafeMonitorError

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
