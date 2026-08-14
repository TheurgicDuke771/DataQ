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
            # Upstream-down HTTP statuses (#1285). A gateway reporting its backend is
            # missing or overloaded is a connectivity fact, not a configuration one.
            #
            # PHRASES ONLY — never the bare numbers. This table is shared by every
            # caller (runs, dry-runs, monitors, comparison, UC, lineage refresh,
            # inventory sync) and CONNECTIVITY is matched before CONFIG, so a bare
            # "503" would capture a sealed-vault error ("HTTP 503 — vault sealed"),
            # a Snowflake "error line 1 at position 504", and a pandas "Expected 3
            # fields in line 5031" — each then reported as "the datasource could not
            # be reached" while the datasource is fine. The digits are redundant
            # anyway: httpx's `raise_for_status()` text carries the reason phrase.
            #
            # Deliberately NOT 404: a 404 from a provider REST API is genuinely
            # ambiguous (deleted DAG vs. stopped host answering at the ingress), so it
            # stays in CONFIG, whose orchestration message names both causes rather
            # than asserting the wrong one.
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
    """The fixed, secret-free user-facing reason for a failed run/dry-run (#605).

    Reads ``exc`` only to pick the category; the returned string is a constant
    from ``_MESSAGES``, so no credential/DSN/PII fragment can ride out on it.
    """
    return _MESSAGES[classify_failure_category(exc)]


def safe_failure_reason(exc: BaseException) -> str:
    """The user-facing reason: verbatim when SAFE-marked, classified otherwise.

    **The single policy** for turning an exception into text a user sees — used
    by the monitor loop (per-check `error_message`), the run path (a run's
    `failure_reason`) and the dry-run preview (the 502 detail). Having one
    implementation is the point: these three sinks are equally unprotected by the
    logger-level scrubber, so a message safe for one is safe for all three and a
    message unsafe for one is unsafe for all three. Three separate isinstance
    branches is how they drift — which they had (#595: the run path grew its own
    narrower copy and the dry-run had none, so a `ScanTooLargeError` naming the
    file, the cap and the knob reached a real run and became "dry run could not
    execute" in the preview of the very same target).

    `SafeMonitorError` is the declared marker (see its docstring for the rule);
    everything else goes through `classify_failure_reason`. An empty message
    falls through to classification too — a marked exception with nothing to say
    is worse than the generic sentence, not better.
    """
    if isinstance(exc, SafeMonitorError) and str(exc):
        return str(exc)
    return classify_failure_reason(exc)


# ── Orchestration-poll classification (#1285) ────────────────────────────────
# Same shape as the inventory-sync case below, in a different place: the generic
# messages are written for a RUN against a datasource, and an orchestration
# connection (ADF / Airflow / dbt) is explicitly NOT a datasource (CLAUDE.md §4).
# It has no warehouse, no role, no table/path and no run target, so `CONFIG`'s
# "e.g. a missing warehouse or role … check the suite's run target" names four
# things that do not exist on the object that failed, and `CONNECTIVITY` /
# `PERMISSION` both say "the datasource".
#
# This was live in prod: both Airflow connections reported the CONFIG message
# while the actual cause was the harness Airflow app being Stopped. A silent gap
# makes an operator look; a confident misdirection makes them look in the wrong
# place — the #828 shape.
#
# CONFIG deliberately names BOTH plausible causes rather than picking one. A
# stopped host and a deleted DAG are genuinely indistinguishable here: the poll
# is an httpx call ending in `raise_for_status()`, and Container Apps answers for
# a stopped app with the same 404 shape as a real "DAG not found".
#
# The wording is also provider-NEUTRAL, which is not a style choice: the three
# providers have genuinely different shapes. Airflow polls a REST host (base
# URL), ADF polls Azure by subscription/resource-group/factory, and dbt contacts
# no host at all — it reads a `run_results.json` artifact from ADLS/S3/file. A
# message that says "check the connection's base URL" is the very defect this
# fixes, one provider over. Likewise nothing here asserts that anything
# *answered*: the exception can be raised before any request leaves DataQ (a
# sealed secret store, an unknown provider, a config validation error).
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
    """The fixed, secret-free reason an orchestration poll failed (#1285).

    Same redaction contract as :func:`classify_failure_reason` — ``exc`` is read
    only to pick a category, and the returned string is a constant — but the
    text names orchestration nouns instead of datasource ones.
    """
    return _ORCHESTRATION_MESSAGES[classify_failure_category(exc)]


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
