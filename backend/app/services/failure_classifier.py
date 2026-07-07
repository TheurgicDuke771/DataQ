"""Redaction-safe classification of a run/dry-run failure into a user reason (#605).

A runner/adapter exception can carry DSN, credential, or PII fragments — a
Snowflake login error may echo the account URL, a file error the storage path.
So we **never store or surface the raw exception text**. We read it only to
*classify* it into one of a small allowlist of categories and return a **fixed
per-category message**. The full exception still goes to the server log
(`log.exception`) for operators; only the safe, generic reason reaches the API.

The classification is a best-effort heuristic over the exception type + message;
the default is the neutral ``unknown`` message, so a miss is never a leak — it
just reads as "see the logs".
"""

from __future__ import annotations

from enum import StrEnum


class FailureCategory(StrEnum):
    CONFIG = "config"
    CONNECTIVITY = "connectivity"
    PERMISSION = "permission"
    UNKNOWN = "unknown"


# Fixed, secret-free messages — the ONLY text that leaves DataQ for a failed run.
_MESSAGES: dict[FailureCategory, str] = {
    FailureCategory.CONFIG: (
        "The connection or run target looks misconfigured — e.g. a missing warehouse "
        "or role, or a table/path that does not exist. Check the connection and the "
        "suite's run target."
    ),
    FailureCategory.CONNECTIVITY: (
        "The datasource could not be reached (network, DNS, TLS, or a timeout). "
        "Check that the datasource is reachable from DataQ."
    ),
    FailureCategory.PERMISSION: (
        "The datasource rejected the credentials, or a required grant/permission is "
        "missing. Re-check the connection's credentials and grants."
    ),
    FailureCategory.UNKNOWN: "The run failed to execute. See the server logs for details.",
}

# Substring markers matched against a lowercased "<ExcType>: <str(exc)>". Ordered
# most-specific-first: permission (auth) is checked before connectivity (a bare
# "connection" token) and config, so "invalid credentials" classifies as
# permission, not config.
_MARKERS: tuple[tuple[FailureCategory, tuple[str, ...]], ...] = (
    (
        FailureCategory.PERMISSION,
        (
            "permission denied",
            "access denied",
            "unauthorized",
            "not authorized",
            "forbidden",
            "authenticat",  # authentication / authenticate / failed to authenticate
            "invalid credential",
            "incorrect username or password",
            "insufficient privile",
            "insufficient permission",
            "grant",
            "login failed",
            "http 401",
            "http 403",
        ),
    ),
    (
        FailureCategory.CONNECTIVITY,
        (
            "timed out",
            "timeout",
            "could not connect",
            "connection refused",
            "connection reset",
            "connection aborted",
            "network is unreachable",
            "unreachable",
            "temporary failure in name resolution",
            "name or service not known",
            "getaddrinfo",
            "max retries exceeded",
            "failed to establish a new connection",
            "ssl",
        ),
    ),
    (
        FailureCategory.CONFIG,
        (
            "does not exist",
            "no such",
            "not found",
            "cannot be found",
            "no active warehouse",
            "unknown database",
            "unknown schema",
            "unknown table",
            "invalid identifier",
            "missing",
            "keyerror",
        ),
    ),
)


def classify_failure_category(exc: BaseException) -> FailureCategory:
    """Best-effort category for a run/dry-run failure. Never raises."""
    haystack = f"{type(exc).__name__}: {exc}".lower()
    for category, markers in _MARKERS:
        if any(marker in haystack for marker in markers):
            return category
    return FailureCategory.UNKNOWN


def classify_failure_reason(exc: BaseException) -> str:
    """The fixed, secret-free user-facing reason for a failed run/dry-run (#605).

    Reads ``exc`` only to pick the category; the returned string is a constant
    from ``_MESSAGES``, so no credential/DSN/PII fragment can ride out on it.
    """
    return _MESSAGES[classify_failure_category(exc)]
