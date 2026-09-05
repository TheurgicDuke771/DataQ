"""Redaction-safe classification of a run/dry-run failure into a user reason (#605)."""

from __future__ import annotations

from enum import StrEnum

from backend.app.core.errors import SafeMonitorError


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

# Substring markers matched against a lowercased "<ExcType>: <str(exc)>".
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
            # Upstream-down HTTP statuses (#1285).
            "bad gateway",
            "service unavailable",
            "gateway timeout",
            "no healthy upstream",
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
    """The fixed, secret-free user-facing reason for a failed run/dry-run (#605)."""
    return _MESSAGES[classify_failure_category(exc)]


def safe_failure_reason(exc: BaseException) -> str:
    """The user-facing reason: verbatim when SAFE-marked, classified otherwise."""
    if isinstance(exc, SafeMonitorError) and str(exc):
        return str(exc)
    return classify_failure_reason(exc)


# ── Orchestration-poll classification (#1285) ──────────────────────────────── Same shape as the
# inventory-sync case below.
_ORCHESTRATION_MESSAGES: dict[FailureCategory, str] = {
    FailureCategory.CONFIG: (
        "DataQ could not get what it asked for from this orchestration provider — the "
        "pipeline/DAG, or the artifact it publishes, may no longer exist, or the "
        "connection may point somewhere that no longer holds it. Check the connection's "
        "settings and the pipeline/DAG id on its trigger bindings."
    ),
    FailureCategory.CONNECTIVITY: (
        "The orchestration provider could not be reached (network, DNS, TLS, a timeout, "
        "or the host or storage it lives on is down). Check that it is running and "
        "reachable from DataQ."
    ),
    FailureCategory.PERMISSION: (
        "The orchestration provider rejected the credentials, or the identity is missing a "
        "permission it needs to read runs. Re-check the connection's credentials and its "
        "access to the pipelines/DAGs."
    ),
    FailureCategory.UNKNOWN: (
        "Polling this orchestration connection failed for a reason DataQ could not "
        "classify. Re-test the connection, and check the worker logs for the underlying "
        "error."
    ),
}


def classify_orchestration_poll_reason(exc: BaseException) -> str:
    """The fixed, secret-free reason an orchestration poll failed (#1285)."""
    return _ORCHESTRATION_MESSAGES[classify_failure_category(exc)]


# ── Inventory-sync grant classification (#1104) ──────────────────────────────
# `sync_connection_inventory` always reads one FIXED.
_INVENTORY_SCHEMA_BY_TYPE: dict[str, str] = {
    "unity_catalog": "`system.information_schema`",
    "snowflake": "INFORMATION_SCHEMA in the connection's database",
}

# The generic messages are written for a RUN, and an inventory sync has neither a run nor a run
# target.
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
    """The fixed, secret-free reason an inventory-sync attempt failed (#1104)."""
    category = classify_failure_category(exc)
    if during_enumeration and category is FailureCategory.PERMISSION:
        schema = _INVENTORY_SCHEMA_BY_TYPE.get(connection_type)
        if schema:
            # Phase narrows this to "the warehouse rejected OUR query", which is as far as the
            # evidence goes: a Snowflake role that lost USAGE on its warehouse is rejected here too.
            return (
                f"The inventory-sync query against {schema} was rejected — most likely a "
                "missing SELECT grant there, or a role/warehouse privilege the query "
                "needs. Grant SELECT (or ask your workspace admin to) and it will resolve "
                "automatically on the next sync; if the grants are already in place, "
                "re-check the connection's role and credentials."
            )
    return _INVENTORY_MESSAGES.get(category) or classify_failure_reason(exc)


# ── Broker (Redis) classification (#1885) ──────────────────────────────────── The admin health
# API's queue-depth read: never a fake `0` on a broker it could not reach.
_BROKER_MESSAGES: dict[FailureCategory, str] = {
    FailureCategory.CONNECTIVITY: (
        "The message broker (Redis) could not be reached (network, DNS, TLS, or a "
        "timeout). Check that it is running and reachable from the API."
    ),
    FailureCategory.PERMISSION: "The message broker (Redis) rejected the credentials.",
    FailureCategory.CONFIG: "The message broker (Redis) looks misconfigured.",
    FailureCategory.UNKNOWN: (
        "Queue depth could not be read for a reason DataQ could not classify. Check the "
        "server logs for the underlying error."
    ),
}


def classify_broker_reason(exc: BaseException) -> str:
    """The fixed, secret-free reason the broker (Redis) could not be reached (#1885)."""
    return _BROKER_MESSAGES[classify_failure_category(exc)]


# ── Datasource credential-health classification (#1697) ──────────────────────
#
# Deliberately NARROWER than `FailureCategory.PERMISSION`, which also covers a missing
# SELECT grant. A missing grant is a fact about an authorization policy; only a rejected
# CREDENTIAL is a fact about the credential's health, and #1697's signal is the latter.
# Markers are the driver-emitted vocabulary for "who you are was rejected".
_AUTH_MARKERS: tuple[str, ...] = (
    # Snowflake 390100-family: bad password / expired token. Not the bare 250001 prefix,
    # which also covers a wrong account locator.
    "390100",
    "390114",
    "390195",
    "incorrect username or password",
    "authentication token has expired",
    "jwt token is invalid",
    "invalid oauth access token",
    # Databricks / Unity Catalog: an invalid or revoked PAT.
    "invalid access token",
    "pat is invalid",
    # Azure ADLS Gen2 / Blob.
    "authenticationfailed",
    "server failed to authenticate the request",
    "signature did not match",
    "invalid_client",
    # AWS S3 (and S3-compatible stores, which reuse the same error codes).
    "invalidaccesskeyid",
    "signaturedoesnotmatch",
    "expiredtoken",
    "tokenrefreshrequired",
    "unrecognizedclientexception",
    # Generic HTTP shape — Iceberg REST catalogs, Databricks SQL, any HTTP driver.
    "http 401",
    "401 unauthorized",
    "invalid credential",
    "credentials could not be resolved",
)

#: The one fixed, secret-free reason a credential-health failure is recorded with.
AUTH_FAILURE_REASON = (
    "The datasource rejected this connection's credential. It has most likely expired "
    "or been revoked — rotate it via the connection's Re-authenticate action."
)


#: How far to walk `__cause__`/`__context__`. Bounded so a self-referential chain cannot spin.
_CAUSE_CHAIN_DEPTH = 5


def is_auth_failure(exc: BaseException) -> bool:
    """Whether ``exc`` is the datasource rejecting the CREDENTIAL itself (#1697).

    A missing grant, a bad table name and an unreachable host are all excluded: none of
    them says anything about whether the stored credential still works.

    Walks the cause chain, because the driver's own message is routinely the *cause* of
    what reaches us — `InventorySyncEnumerationError` and GX/SQLAlchemy wrappers all
    re-raise `from` the original, and matching only the outermost type would see none
    of the vocabulary the datasource actually emitted.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    for _ in range(_CAUSE_CHAIN_DEPTH):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        haystack = f"{type(current).__name__}: {current}".lower()
        if any(marker in haystack for marker in _AUTH_MARKERS):
            return True
        current = current.__cause__ or current.__context__
    return False
