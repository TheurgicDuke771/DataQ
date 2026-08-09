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


# ── Inventory-sync grant classification (#1104) ──────────────────────────────
# `sync_connection_inventory` always reads one FIXED, KNOWN system schema per
# connection type (see `backend/app/lineage/warehouse_{snowflake,unity_catalog}.py`
# `enumerate_tables`). That schema name is safe to state in the reason because
# DataQ chose the query — it is never parsed back out of the driver's own error
# text, which would repeat the #536 traceback-locals leak shape laundered
# through a regex.
_INVENTORY_SCHEMA_BY_TYPE: dict[str, str] = {
    "unity_catalog": "`system.information_schema`",
    "snowflake": "INFORMATION_SCHEMA in the connection's database",
}

# The generic messages are written for a RUN, and an inventory sync has neither a
# run nor a run target — `CONFIG` points the reader at "the suite's run target"
# (there isn't one) and `UNKNOWN` says "the run failed to execute. See the server
# logs", which is exactly the answer #1104 exists to replace. Categories whose
# generic text is already datasource-centric (`CONNECTIVITY`) are left alone
# rather than reworded for its own sake.
_INVENTORY_MESSAGES: dict[FailureCategory, str] = {
    FailureCategory.CONFIG: (
        "The inventory-sync query could not resolve what it was asked to read — e.g. a "
        "missing warehouse or role, or a database/catalog that no longer exists. Check "
        "the connection's settings."
    ),
    FailureCategory.UNKNOWN: (
        "The inventory sync failed for a reason DataQ could not classify. The rest of the "
        "connection may still be healthy — re-test it, and check the worker logs for the "
        "underlying error."
    ),
}


def classify_inventory_sync_error(
    exc: BaseException, connection_type: str, *, during_enumeration: bool
) -> str:
    """The fixed, secret-free reason an inventory-sync attempt failed (#1104).

    A missing grant gets a SPECIFIC message naming the system schema the sync
    reads — this is the #828 shape the issue exists to close: toggle on,
    connection test green (the `SELECT 1` probe never exercises this query),
    zero assets ever appear, no surface says why. Every other failure category
    (connectivity/config/unknown) falls back to the generic
    :func:`classify_failure_reason` message, unchanged.

    ``during_enumeration`` is REQUIRED, and is the phase the failure actually
    happened in — ``True`` only once the warehouse connection is open and the
    enumeration query itself is running (`InventorySyncEnumerationError` in
    `inventory_service`). The category markers are broad substrings, so a
    failure that never reached the warehouse at all — a sealed vault or a 403
    from the secret store while resolving the credential, an IdP rejecting the
    token during the driver handshake — matches PERMISSION just as readily as a
    missing `SELECT`. Naming a warehouse grant for one of those sends an admin
    to fix a privilege that was never the problem while the real fault stays
    undiagnosed, which is a *worse* failure than the generic message: a
    confident wrong answer, the exact #828 shape this feature exists to end.
    Phase is the one thing we know for certain, so it gates the specific claim;
    everything else falls back to the hedged generic reason.
    """
    category = classify_failure_category(exc)
    if during_enumeration and category is FailureCategory.PERMISSION:
        schema = _INVENTORY_SCHEMA_BY_TYPE.get(connection_type)
        if schema:
            # Phase narrows this to "the warehouse rejected OUR query", which is as
            # far as the evidence goes: a Snowflake role that lost USAGE on its
            # warehouse is rejected here too, and reads identically. So the message
            # names the most likely cause and the two other privileges worth
            # checking, rather than asserting one of them.
            return (
                f"The inventory-sync query against {schema} was rejected — most likely a "
                "missing SELECT grant there, or a role/warehouse privilege the query "
                "needs. Grant SELECT (or ask your workspace admin to) and it will resolve "
                "automatically on the next sync; if the grants are already in place, "
                "re-check the connection's role and credentials."
            )
    return _INVENTORY_MESSAGES.get(category) or classify_failure_reason(exc)
