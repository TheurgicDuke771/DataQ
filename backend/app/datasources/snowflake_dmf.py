"""Snowflake Data Metric Functions — the first platform-native check engine
(ADR 0036 §6, #895 slice 2).

Ad-hoc ``SELECT`` invocation of the system DMFs, mapped onto EXISTING kinds
(ADR 0036 §4 — engines are alternate evaluators, never new kinds):

* kind ``freshness`` → ``SNOWFLAKE.CORE.FRESHNESS(SELECT <col> FROM <t>)`` —
  seconds since the column's max, converted to **age-hours** so the metric is
  the same unit the GX-path freshness monitor records and trends stay
  comparable across engines. The DMF accepts **DATE / TIMESTAMP_LTZ /
  TIMESTAMP_TZ only** — TIMESTAMP_NTZ is refused by Snowflake (live-verified;
  the argument must project a bare column, so casting inside is not an
  option), and `_classify_dmf_error` turns that refusal into guidance naming
  the GX-engine freshness monitor as the path for NTZ columns.
* kind ``volume`` is deliberately NOT here, despite #895's original list:
  ``SNOWFLAKE.CORE.ROW_COUNT`` **cannot be invoked ad hoc** ("You can't call
  this function directly" — it exists only attached to a table on a schedule;
  live-verified 2026-08-22: the call fails however the argument is spelled).
  The GX-path volume monitor's ``COUNT(*)`` already pushes down identically,
  so nothing is lost; warehouse-scheduled DMF ingest is the follow-up ADR.
* kind ``expectation`` → the four column metrics (``dmf:null_count`` /
  ``dmf:null_percent`` / ``dmf:duplicate_count`` / ``dmf:unique_count``): a
  scalar → ``metric_value``, banded by the check's thresholds in the run
  service exactly like a monitor. ``dmf:unique_count`` refuses thresholds at
  author time — `severity.derive_status` bands higher-as-worse, and a unique
  count degrades DOWNWARD, so a threshold would invert its meaning; it runs as
  an informational metric (trend / future anomaly input).

Identifiers are validated by the shared #428 allowlist and then quoted by the
#476/#937 folding rule (all-lower-case → bare so the warehouse folds it;
anything else → quoted) — string interpolation is unavoidable here because the
``FUNC(SELECT …)`` argument is Snowflake-specific syntax SQLAlchemy Core cannot
express, which is also why every interpolated name goes through the allowlist
first.

Run-time failures are classified per check (`safe_failure_reason`) — a DMF
privilege/edition error must land as that check's ``error`` result with
actionable guidance, never raw driver text (the #828/#900 rule) and never a
raise that takes the suite's GX siblings down (ADR 0036 §5).

Custom DMFs and warehouse-scheduled DMF ingest are follow-ups (the latter needs
its own ADR — it is a pull provider, not a `CheckRunner`; see ADR 0036 §6).
"""

from __future__ import annotations

from typing import Any

from backend.app.datasources.base import CheckOutcome
from backend.app.datasources.monitors import (
    FRESHNESS,
    MonitorConfigError,
    monitor_expectation_type,
)
from backend.app.datasources.sql import is_sql_identifier
from backend.app.services.failure_classifier import safe_failure_reason

DMF_ENGINE = "dmf"

# expectation-kind metric types → the system DMF that computes them. The
# ``dmf:`` prefix keeps the stored row self-describing (the `monitor:` pattern)
# and unreachable by the GX validator, so neither engine can evaluate the
# other's types by accident.
DMF_COLUMN_METRICS: dict[str, str] = {
    "dmf:null_count": "NULL_COUNT",
    "dmf:null_percent": "NULL_PERCENT",
    "dmf:duplicate_count": "DUPLICATE_COUNT",
    "dmf:unique_count": "UNIQUE_COUNT",
}
DMF_EXPECTATION_TYPES = tuple(DMF_COLUMN_METRICS)
# Higher-is-worse metrics band with thresholds; unique_count degrades DOWNWARD
# so thresholds are refused at author time (see module docstring).
DMF_UNBANDABLE_TYPES = frozenset({"dmf:unique_count"})
# The engine's supported-kind matrix (ADR 0036 §4). comparison / schema_drift /
# anomaly have run paths DMF cannot express (cross-dataset diff, introspection,
# baseline state), and volume is excluded because ROW_COUNT has no ad-hoc form
# (module docstring) — authoring any of them with engine='dmf' is a 422, not a
# run error.
DMF_KINDS = ("expectation", FRESHNESS)


def _quoted(name: object, *, what: str) -> str:
    """Validate (allowlist, #428) then quote (folding rule, #476/#937) a name."""
    if not is_sql_identifier(name):
        raise MonitorConfigError(f"invalid {what} identifier: {name!r}")
    assert isinstance(name, str)
    return name if name == name.lower() else f'"{name}"'


def build_dmf_statement(
    *,
    kind: str,
    expectation_type: str,
    config: dict[str, Any],
    table: str,
    schema: str | None,
) -> str:
    """The ad-hoc invocation ``SELECT SNOWFLAKE.CORE.<FN>(SELECT … FROM <t>)``.

    Raises `MonitorConfigError` for a kind/type outside the matrix or a bad
    identifier — author-time validation (`check_service`) rejects both, so
    hitting one here means an out-of-band row; it becomes that check's
    classified ``error``.
    """
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


def _freshness_outcome(scalar: Any, config: dict[str, Any]) -> CheckOutcome:
    """Seconds-since-max → the monitor freshness outcome shape (age-hours metric).

    Mirrors `monitors._freshness_outcome` deliberately — same metric unit, same
    observed/expected keys — so a check moved between engines keeps a coherent
    trend. The DMF returns the AGE directly, so no ``max_timestamp`` is
    available to report; fabricating one from ``now - age`` would present a
    derived value as an observation, so the field is simply absent.
    """
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
    # Clamped at 0 like the monitor path (`_freshness_age_hours`): a max
    # timestamp ahead of the warehouse clock must trend 0.0 on BOTH engines,
    # or the same data trends differently per evaluator.
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
        "metric": DMF_COLUMN_METRICS[expectation_type],
        "column": config.get("column"),
    }
    if scalar is None:
        return CheckOutcome(
            expectation_type=expectation_type,
            success=False,
            errored=True,
            error_message=f"{DMF_COLUMN_METRICS[expectation_type]} returned no value",
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

    ``fetch_scalar`` executes the SQL text and returns its scalar — the runner
    closes over its shared engine (#427), keeping this module unit-testable
    with no warehouse.
    """
    try:
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
        # Classified, never raw (#900): a Snowflake DMF failure text can carry
        # the statement (and DMF privilege errors name objects). The DMF-shaped
        # failures get specific, actionable guidance (ADR 0036 §3 — "what the
        # limitation is and what access to request"); everything else falls to
        # the shared classifier. Pattern-matching READS the raw text; none of
        # it is ever echoed.
        return CheckOutcome(
            expectation_type=expectation_type,
            success=False,
            errored=True,
            error_message=_classify_dmf_error(exc),
        )


def _classify_dmf_error(exc: Exception) -> str:
    """Fixed guidance for the DMF failure shapes live testing surfaced —
    Snowflake's own messages either mislead when generically classified (an
    unknown column read as "connection misconfigured") or name a limitation
    the author can only fix by knowing the platform rule."""
    text = str(exc)
    if "Invalid argument types" in text and "FRESHNESS" in text:
        return (
            "Snowflake's FRESHNESS data metric function accepts DATE, "
            "TIMESTAMP_LTZ and TIMESTAMP_TZ columns only — this column's type "
            "(commonly TIMESTAMP_NTZ) is not supported by the DMF. Use the "
            "GX-engine freshness monitor for this column instead."
        )
    if "invalid identifier" in text or "does not exist or not authorized" in text:
        # The second shape is Snowflake's missing-TABLE error (002003) — its
        # "or not authorized" tail must not fall through to the privilege
        # branch below, which would prescribe grants for a dropped table.
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
