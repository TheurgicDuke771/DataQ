"""Snowflake Data Metric Functions — the first platform-native check engine
(ADR 0036 §6, #895 slice 2).
"""

from __future__ import annotations

from typing import Any

from backend.app.core.logging import get_logger
from backend.app.datasources.base import CheckOutcome
from backend.app.datasources.monitors import (
    FRESHNESS,
    MonitorConfigError,
    _echo,
    _ident,
    monitor_expectation_type,
)
from backend.app.datasources.sql import is_sql_identifier
from backend.app.services.failure_classifier import safe_failure_reason

log = get_logger(__name__)

DMF_ENGINE = "dmf"

# expectation-kind metric types → the system DMF that computes them.
DMF_COLUMN_METRICS: dict[str, str] = {
    "dmf:null_count": "NULL_COUNT",
    "dmf:null_percent": "NULL_PERCENT",
    "dmf:duplicate_count": "DUPLICATE_COUNT",
    "dmf:unique_count": "UNIQUE_COUNT",
    "dmf:blank_count": "BLANK_COUNT",
    "dmf:future_timestamp_percent": "FUTURE_TIMESTAMP_PERCENT",
}
# ACCEPTED_VALUES cannot be called ad hoc (it needs an attached, scheduled DMF), but
# SYSTEM$DATA_METRIC_SCAN evaluates it with no attachment and only SELECT rights (#2084).
DMF_ACCEPTED_VALUES = "dmf:accepted_values"
# A customer-defined DMF (`CREATE DATA METRIC FUNCTION`), named by its fully qualified identifier
# and called ad hoc over bare columns of the target, like the system ones (#2226).
DMF_CUSTOM = "dmf:custom"
DMF_EXPECTATION_TYPES = (*DMF_COLUMN_METRICS, DMF_ACCEPTED_VALUES, DMF_CUSTOM)
#: The config keys each dmf:* expectation type takes.
DMF_CONFIG_KEYS: dict[str, frozenset[str]] = {
    **{t: frozenset({"column"}) for t in DMF_COLUMN_METRICS},
    DMF_ACCEPTED_VALUES: frozenset({"column", "value_set"}),
    DMF_CUSTOM: frozenset({"function", "columns"}),
}
_MAX_CUSTOM_COLUMNS = 20
_MAX_FUNCTION_NAME_CHARS = 770  # three 255-char identifiers and two dots
_METRIC_NAMES = {**DMF_COLUMN_METRICS, DMF_ACCEPTED_VALUES: "ACCEPTED_VALUES"}
_MAX_ACCEPTED_VALUES = 500
_MAX_VALUE_CHARS = 1_000
# Higher-is-worse metrics band with thresholds; unique_count degrades DOWNWARD
# so thresholds are refused at author time (see module docstring).
DMF_UNBANDABLE_TYPES = frozenset({"dmf:unique_count"})
# The engine's supported-kind matrix (ADR 0036 §4). comparison / schema_drift / anomaly have run
# paths DMF cannot express (cross-dataset diff, introspection, baseline state).
DMF_KINDS = ("expectation", FRESHNESS)


def _quoted(name: object, *, what: str) -> str:
    """Validate (allowlist, #428 — via `_ident`, bounded echo) then quote (#476/#937)."""
    ident = _ident(name, what=what)
    return ident if ident == ident.lower() else f'"{ident}"'


def build_dmf_statement(
    *,
    kind: str,
    expectation_type: str,
    config: dict[str, Any],
    table: str,
    schema: str | None,
) -> str:
    """The ad-hoc invocation ``SELECT SNOWFLAKE.CORE.<FN>(SELECT … FROM <t>)``."""
    target = _quoted(table, what="table")
    if schema is not None:
        target = f"{_quoted(schema, what='schema')}.{target}"
    if kind == FRESHNESS:
        column = _quoted(config.get("column"), what="freshness column")
        return f"SELECT SNOWFLAKE.CORE.FRESHNESS(SELECT {column} FROM {target})"  # noqa: S608  # nosec B608
    function = DMF_COLUMN_METRICS.get(expectation_type)
    if kind != "expectation" or function is None:
        raise MonitorConfigError(
            f"the dmf engine cannot evaluate kind {kind!r} / type {expectation_type!r}"
        )
    column = _quoted(config.get("column"), what="column")
    return f"SELECT SNOWFLAKE.CORE.{function}(SELECT {column} FROM {target})"  # noqa: S608  # nosec B608


def parse_custom_dmf_name(name: object) -> tuple[str, str, str]:
    """``<database>.<schema>.<function>``, each part through the identifier allowlist (#428).

    The system DMFs have their own check types, so the ``SNOWFLAKE`` database is refused:
    called this way, ACCEPTED_VALUES and SCHEMA_CHANGE_COUNT return the sentinel ``-1``.
    """
    if not isinstance(name, str) or len(name) > _MAX_FUNCTION_NAME_CHARS:
        raise MonitorConfigError(
            "'function' must be a fully qualified name <database>.<schema>.<function>, "
            f"got {_echo(name)}"
        )
    parts = name.split(".")
    if len(parts) != 3 or not all(is_sql_identifier(part) for part in parts):
        raise MonitorConfigError(
            "'function' must be a fully qualified name <database>.<schema>.<function> of plain "
            f"identifiers (letters, digits, _ and $), got {_echo(name)}"
        )
    if parts[0].upper() == "SNOWFLAKE":
        raise MonitorConfigError(
            "'function' names a Snowflake system DMF — use its own DMF check type instead"
        )
    return parts[0], parts[1], parts[2]


def custom_dmf_columns(config: dict[str, Any]) -> list[str]:
    """The DMF's arguments: 1 to 20 bare columns of the suite's target, in signature order."""
    columns = config.get("columns")
    if not isinstance(columns, list) or not 1 <= len(columns) <= _MAX_CUSTOM_COLUMNS:
        raise MonitorConfigError(
            f"'columns' must be a list of 1 to {_MAX_CUSTOM_COLUMNS} column names, in the order "
            "the DMF's TABLE(...) argument declares them"
        )
    return [_ident(column, what="column") for column in columns]


def build_custom_dmf_statement(config: dict[str, Any], *, table: str, schema: str | None) -> str:
    """The ad-hoc invocation ``SELECT <db>.<schema>.<dmf>(SELECT <col>[, …] FROM <t>)``."""
    database, dmf_schema, name = parse_custom_dmf_name(config.get("function"))
    function = ".".join(
        (
            _quoted(database, what="DMF database"),
            _quoted(dmf_schema, what="DMF schema"),
            _quoted(name, what="DMF name"),
        )
    )
    columns = ", ".join(_quoted(c, what="column") for c in custom_dmf_columns(config))
    target = _quoted(table, what="table")
    if schema is not None:
        target = f"{_quoted(schema, what='schema')}.{target}"
    return f"SELECT {function}(SELECT {columns} FROM {target})"  # noqa: S608  # nosec B608


def _sql_literal(value: Any) -> str:
    """A value as a SQL literal inside the scan's expression: numbers bare, strings quoted."""
    if isinstance(value, bool) or value is None:
        raise MonitorConfigError("each accepted value must be a number or a string")
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, str) and len(value) <= _MAX_VALUE_CHARS:
        # Snowflake string literals process backslash escapes, so a backslash is doubled before
        # the quote: `x\') OR TRUE --` must not close the literal early.
        return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"
    raise MonitorConfigError(
        f"each accepted value must be a number or a string of at most {_MAX_VALUE_CHARS}"
    )


def build_accepted_values_scan(
    config: dict[str, Any], *, table: str, schema: str | None
) -> tuple[str, dict[str, str]]:
    """``COUNT(*)`` over ``SYSTEM$DATA_METRIC_SCAN`` — the violating rows, NULLs excluded.

    Every argument is a bind parameter, so the only SQL DataQ assembles is the accepted-values
    expression, from an allowlisted column and escaped literals.
    """
    column = _quoted(config.get("column"), what="column")
    values = config.get("value_set")
    if not isinstance(values, list) or not 1 <= len(values) <= _MAX_ACCEPTED_VALUES:
        raise MonitorConfigError(
            f"'value_set' must be a list of 1 to {_MAX_ACCEPTED_VALUES} values"
        )
    target = _quoted(table, what="table")
    if schema is not None:
        target = f"{_quoted(schema, what='schema')}.{target}"
    statement = (
        "SELECT COUNT(*) FROM TABLE(SYSTEM$DATA_METRIC_SCAN("
        "REF_ENTITY_NAME => :ref, METRIC_NAME => 'SNOWFLAKE.CORE.ACCEPTED_VALUES', "
        "ARGUMENT_NAME => :arg, ARGUMENT_EXPRESSION => :expr))"
    )
    expression = f"{column} IN ({', '.join(_sql_literal(v) for v in values)})"
    return statement, {"ref": target, "arg": column, "expr": expression}


def _freshness_outcome(scalar: Any, config: dict[str, Any]) -> CheckOutcome:
    """Seconds-since-max → the monitor freshness outcome shape (age-hours metric)."""
    expectation_type = monitor_expectation_type(FRESHNESS)
    expected = {"monitor": FRESHNESS, "column": config.get("column"), "engine": DMF_ENGINE}
    if scalar is None:
        # An empty table has no max — same no-verdict rule as the monitor path.
        return CheckOutcome(
            expectation_type=expectation_type,
            success=False,
            errored=True,
            error_message="FRESHNESS returned no value (empty table?), freshness can't be assessed",
            expected_value=expected,
        )
    # Clamped at 0 like the monitor path (`_freshness_age_hours`): a max timestamp ahead of the
    # warehouse clock must trend 0.0 on BOTH engines, or the same data trends differently per
    # evaluator.
    age_hours = max(float(scalar) / 3600.0, 0.0)
    return CheckOutcome(
        expectation_type=expectation_type,
        success=True,  # binary fallback; thresholds band the age (authoring requires one)
        metric_value=age_hours,
        observed_value={"age_hours": round(age_hours, 3)},
        expected_value=expected,
    )


def _column_metric_outcome(
    scalar: Any, *, expectation_type: str, config: dict[str, Any]
) -> CheckOutcome:
    expected = {
        "engine": DMF_ENGINE,
        "metric": _METRIC_NAMES[expectation_type],
        "column": config.get("column"),
    }
    if scalar is None:
        return CheckOutcome(
            expectation_type=expectation_type,
            success=False,
            errored=True,
            error_message=f"{_METRIC_NAMES[expectation_type]} returned no value",
            expected_value=expected,
        )
    value = float(scalar)
    return CheckOutcome(
        expectation_type=expectation_type,
        success=True,  # thresholds band it (unique_count stays informational)
        metric_value=value,
        observed_value={"value": value},
        expected_value=expected,
    )


def _custom_outcome(scalar: Any, config: dict[str, Any]) -> CheckOutcome:
    expected = {
        "engine": DMF_ENGINE,
        "metric": config.get("function"),
        "columns": config.get("columns"),
    }
    if scalar is None:
        return CheckOutcome(
            expectation_type=DMF_CUSTOM,
            success=False,
            errored=True,
            error_message="the custom DMF returned NULL, so there is no metric to band",
            expected_value=expected,
        )
    value = float(scalar)
    return CheckOutcome(
        expectation_type=DMF_CUSTOM,
        success=True,  # thresholds band it (authoring requires a fail or critical one)
        metric_value=value,
        observed_value={"value": value},
        expected_value=expected,
    )


def evaluate_dmf_check(
    fetch_scalar: Any,
    *,
    kind: str,
    expectation_type: str,
    config: dict[str, Any],
    table: str,
    schema: str | None,
) -> CheckOutcome:
    """Evaluate ONE dmf-engine check; never raises — a failure is that check's
    classified ``error`` outcome (ADR 0036 §5), computed per check so a
    privilege problem on one metric never silences its siblings.
    """
    try:
        if kind == "expectation" and expectation_type == DMF_CUSTOM:
            scalar = fetch_scalar(build_custom_dmf_statement(config, table=table, schema=schema))
            return _custom_outcome(scalar, config)
        if kind == "expectation" and expectation_type == DMF_ACCEPTED_VALUES:
            statement, params = build_accepted_values_scan(config, table=table, schema=schema)
            scalar = fetch_scalar(statement, params)
            return _column_metric_outcome(scalar, expectation_type=expectation_type, config=config)
        statement = build_dmf_statement(
            kind=kind,
            expectation_type=expectation_type,
            config=config,
            table=table,
            schema=schema,
        )
        scalar = fetch_scalar(statement)
        if kind == FRESHNESS:
            return _freshness_outcome(scalar, config)
        return _column_metric_outcome(scalar, expectation_type=expectation_type, config=config)
    except Exception as exc:
        # Classified, never raw (#900): a Snowflake DMF failure text can carry the statement (and
        # DMF privilege errors name objects).
        classify = (
            _classify_custom_dmf_error if expectation_type == DMF_CUSTOM else _classify_dmf_error
        )
        return CheckOutcome(
            expectation_type=expectation_type,
            success=False,
            errored=True,
            error_message=classify(exc),
        )


# The types NULL_COUNT is defined over (live `SHOW DATA METRIC FUNCTIONS`, 2026-09-27), as
# INFORMATION_SCHEMA.COLUMNS.DATA_TYPE spells them.
_PROBE_TARGET_BASE = (
    "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
    "WHERE DATA_TYPE IN ('TEXT', 'NUMBER', 'FLOAT', 'DATE', 'TIMESTAMP_LTZ', "
    "'TIMESTAMP_NTZ', 'TIMESTAMP_TZ') AND TABLE_SCHEMA "
)
# The configured schema first, then anywhere else in the connection's database.
_PROBE_TARGET_QUERIES = (
    _PROBE_TARGET_BASE + "= CURRENT_SCHEMA() LIMIT 1",
    _PROBE_TARGET_BASE + "<> 'INFORMATION_SCHEMA' LIMIT 1",
)

DMF_AVAILABLE = "available"
DMF_NO_PRIVILEGE = "no_privilege"
DMF_UNSUPPORTED_EDITION = "unsupported_edition"
DMF_UNDETERMINED = "undetermined"


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _undetermined(reason: str) -> dict[str, Any]:
    return {"available": None, "status": DMF_UNDETERMINED, "reason": reason}


def probe_dmf_capability(fetch_row: Any) -> dict[str, Any]:
    """Connection-test-time DMF availability probe (#1867, #2112). Never raises;
    the result is the `engine_capabilities["dmf"]` shape.

    An ad-hoc system-DMF call only accepts a bare column of a table or view, so the
    probe borrows one the role can already see and reads zero rows of it
    (``LIMIT 0``: compiled, privilege-checked, nothing scanned). ``available`` is
    ``None`` when the probe itself couldn't decide — never ``False``.
    """
    try:
        target = None
        for query in _PROBE_TARGET_QUERIES:
            target = fetch_row(query)
            if target:
                break
    except Exception as exc:
        log.warning("dmf_probe_target_lookup_failed", error_type=type(exc).__name__)
        return _undetermined(
            "couldn't list a table or view in the connection's database to probe DMF "
            "availability with — it is re-checked on the next connection test"
        )
    if not target:
        return _undetermined(
            "no table or view with a probe-able column is visible to this role in the "
            "connection's database, so DMF availability couldn't be checked — it is "
            "re-checked on the next connection test"
        )
    schema, table, column = (_quote_identifier(str(part)) for part in target)
    statement = f"SELECT SNOWFLAKE.CORE.NULL_COUNT(SELECT {column} FROM {schema}.{table} LIMIT 0)"  # noqa: S608  # nosec B608
    try:
        fetch_row(statement)
    except Exception as exc:
        return _classify_probe_failure(exc)
    return {"available": True, "status": DMF_AVAILABLE}


def _classify_probe_failure(exc: Exception) -> dict[str, Any]:
    text = str(exc)
    if "Unsupported feature" in text or "Enterprise Edition" in text:
        return {
            "available": False,
            "status": DMF_UNSUPPORTED_EDITION,
            "reason": "this Snowflake account's edition does not support data metric "
            "functions — DMFs need Enterprise Edition or higher",
        }
    if (
        "Unknown function" in text
        or "Insufficient privileges" in text
        or "SQL access control error" in text
    ):
        return {
            "available": False,
            "status": DMF_NO_PRIVILEGE,
            "reason": "the connection's role cannot invoke Snowflake system data metric "
            "functions — grant it the SNOWFLAKE.DATA_METRIC_USER database role",
        }
    # Anything else (a broken view, a timeout, an unrecognised message) says nothing about DMF
    # availability either way.
    log.warning("dmf_probe_inconclusive", error_type=type(exc).__name__)
    return _undetermined(
        "DMF availability couldn't be determined from the probe query — it is "
        "re-checked on the next connection test"
    )


_TEMPORAL_TYPES = "DATE, TIMESTAMP_LTZ and TIMESTAMP_TZ columns only"
_ARGUMENT_TYPE_GUIDANCE: dict[str, str] = {
    "FUTURE_TIMESTAMP_PERCENT": (
        f"Snowflake's FUTURE_TIMESTAMP_PERCENT data metric function accepts {_TEMPORAL_TYPES} "
        "— this column's type (commonly TIMESTAMP_NTZ) is not supported by the DMF."
    ),
    "BLANK_COUNT": (
        "Snowflake's BLANK_COUNT data metric function accepts VARCHAR columns only — "
        "point the check at a string column."
    ),
    "FRESHNESS": (
        f"Snowflake's FRESHNESS data metric function accepts {_TEMPORAL_TYPES} — this "
        "column's type (commonly TIMESTAMP_NTZ) is not supported by the DMF. Use the "
        "GX-engine freshness monitor for this column instead."
    ),
}


def _classify_dmf_error(exc: Exception) -> str:
    """Fixed guidance for the DMF failure shapes live testing surfaced —
    Snowflake's own messages either mislead when generically classified (an
    unknown column read as "connection misconfigured") or name a limitation
    the author can only fix by knowing the platform rule.
    """
    text = str(exc)
    if "Invalid argument types" in text:
        for function, guidance in _ARGUMENT_TYPE_GUIDANCE.items():
            if function in text:
                return guidance
    if "invalid identifier" in text or "does not exist or not authorized" in text:
        # The second shape is Snowflake's missing-TABLE error (002003) — its "or not authorized"
        # tail must not fall through to the privilege branch below.
        return (
            "the configured column or table does not exist on the run target "
            "(or the role cannot see it) — check the check's column and the "
            "suite's run target"
        )
    if "Unknown function" in text or "not authorized" in text or "Insufficient privileges" in text:
        return (
            "the connection's role cannot invoke Snowflake system data metric "
            "functions — DMFs need Enterprise Edition and the "
            "SNOWFLAKE.DATA_METRIC_USER database role (or EXECUTE DATA METRIC "
            "FUNCTION) granted to the connection's role"
        )
    return safe_failure_reason(exc)


def _classify_custom_dmf_error(exc: Exception) -> str:
    """Fixed guidance for a custom DMF (#2226), from the live Snowflake shapes. A DMF the role
    has no USAGE on fails exactly like one that does not exist (002141), so one message covers both.
    """
    if isinstance(exc, MonitorConfigError):
        return str(exc)
    text = str(exc)
    if "Unknown user-defined function" in text or "Unknown function" in text:
        return (
            "the custom DMF does not exist, or the connection's role cannot use it — grant "
            "the role USAGE on the DMF and on its database and schema"
        )
    if "Invalid argument types" in text:
        return (
            "the check's columns don't match the custom DMF's signature — list exactly the "
            "columns its TABLE(...) argument declares, in order, with compatible types"
        )
    if "invalid identifier" in text:
        return "a configured column does not exist on the run target (or the role cannot see it)"
    if "does not exist or not authorized" in text:
        return (
            "the run target, or the custom DMF's schema, does not exist or the role cannot "
            "see it — check the suite's run target and the role's USAGE grants"
        )
    if "Unsupported feature" in text or "Enterprise Edition" in text:
        return "data metric functions need Snowflake Enterprise Edition or higher"
    return safe_failure_reason(exc)


# SHOW needs no warehouse and lists only the DMFs the role can use: live-verified 2026-09-29, a
# non-admin role saw a custom DMF only once it held USAGE on it.
_CUSTOM_DMF_LISTING = "SHOW DATA METRIC FUNCTIONS IN ACCOUNT"
MAX_LISTED_CUSTOM_DMFS = 200
_MAX_SIGNATURE_CHARS = 300


def _referenceable(part: str) -> bool:
    # DataQ leaves an all-lower-case identifier unquoted (#937), so a DMF created as a quoted
    # lower-case name would fold to upper case and never resolve.
    return is_sql_identifier(part) and (part != part.lower() or not any(c.isalpha() for c in part))


def list_custom_dmfs(fetch_all: Any) -> dict[str, Any]:
    """The custom-DMF half of the ``dmf`` capability (#2226). Never raises.

    ``custom_functions`` is ``None`` when the listing itself failed, else the visible DMFs
    outside the ``SNOWFLAKE`` database that a check can name.
    """
    try:
        rows = fetch_all(_CUSTOM_DMF_LISTING)
    except Exception as exc:
        log.warning("dmf_custom_listing_failed", error_type=type(exc).__name__)
        return {
            "custom_functions": None,
            "custom_functions_reason": "custom DMFs couldn't be listed for this connection's "
            "role — you can still name one; the list is refreshed on the next connection test",
        }
    listed: dict[tuple[str, str], dict[str, str]] = {}
    unlisted = 0
    for row in rows:
        parts = tuple(str(row.get(key) or "") for key in ("catalog_name", "schema_name", "name"))
        if parts[0].upper() == "SNOWFLAKE":
            continue
        if not all(_referenceable(part) for part in parts):
            unlisted += 1
            continue
        arguments = str(row.get("arguments") or "")
        signature = arguments.removeprefix(parts[2]).strip()[:_MAX_SIGNATURE_CHARS]
        qualified = ".".join(parts)
        listed[(qualified, signature)] = {"name": qualified, "signature": signature}
    functions = [listed[key] for key in sorted(listed)]
    result: dict[str, Any] = {
        "custom_functions": functions[:MAX_LISTED_CUSTOM_DMFS],
        "custom_functions_truncated": len(functions) > MAX_LISTED_CUSTOM_DMFS,
    }
    if unlisted:
        result["custom_functions_unlisted"] = unlisted
    return result
