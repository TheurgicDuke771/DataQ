"""Severity post-processing — derive a result's tier from check thresholds."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from backend.app.datasources.base import CheckOutcome
from backend.app.services.custom_sql import CUSTOM_SQL_EXPECTATION_TYPE

# GX key carrying the violated-row fraction (0-100), copied into sample_failures
# by the runner. This is the badness scalar the thresholds band.
_UNEXPECTED_PERCENT_KEY = "unexpected_percent"

# GX result key carrying the unexpected row COUNT for a custom-SQL check
# (`UnexpectedRowsExpectation` has no `unexpected_percent`).
_OBSERVED_VALUE_KEY = "observed_value"


def extract_metric(outcome: CheckOutcome) -> Decimal | None:
    """The numeric badness scalar for a check, or None if it has none."""
    raw: Any
    if outcome.metric_value is not None:
        raw = outcome.metric_value
    else:
        sample: dict[str, Any] | None = outcome.sample_failures
        if sample and sample.get(_UNEXPECTED_PERCENT_KEY) is not None:
            raw = sample[_UNEXPECTED_PERCENT_KEY]
        elif outcome.expectation_type == CUSTOM_SQL_EXPECTATION_TYPE:
            observed = outcome.observed_value
            observed_count = observed.get(_OBSERVED_VALUE_KEY) if observed else None
            if observed_count is None:
                return None
            raw = observed_count
        else:
            return None
    try:
        metric = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None
    # GX can emit NaN/inf — e.g. unexpected_percent on an empty table is 0/0 (the same reason
    # run_service runs a NaN->null sanitizer).
    return metric if metric.is_finite() else None


#: What a kind's own `metric_value` measures (the monitors set it themselves, see `extract_metric`).
_KIND_METRIC_MEANINGS = {
    "freshness": "age of the newest record, in hours",
    "volume": "percent the row count falls outside the allowed range (0 = in range)",
    "schema_drift": "number of columns that changed",
    "anomaly": "z-score against the check's own baseline (how many standard deviations off)",
    "aggregate": "the aggregate's computed value",
    "comparison": "percent of compared rows that don't match",
}


def metric_meaning(kind: str | None, expectation_type: str | None) -> str | None:
    """What a result's `metric_value` measures, so a reader with no UI (an LLM, #2296) doesn't
    have to guess its direction; ``None`` when this module doesn't know."""
    if kind in _KIND_METRIC_MEANINGS:
        return _KIND_METRIC_MEANINGS[kind]
    etype = expectation_type or ""
    if etype.startswith("dqx:"):
        return "number of rows failing the rule"
    if etype == "dmf:custom":
        return "the value returned by the customer-defined Snowflake data metric function"
    if etype.startswith("dmf:"):
        return f"the Snowflake system data metric {etype.removeprefix('dmf:').upper()}"
    if etype == CUSTOM_SQL_EXPECTATION_TYPE:
        return "number of rows the failing-rows query returned"
    # Only a known expectation: a missing check (kind None) must not be told "percent failing".
    if kind == "expectation" or (kind is None and etype.startswith("expect_")):
        return "percent of rows failing the expectation (higher is worse)"
    return None


def resolve_status(
    outcome: CheckOutcome,
    *,
    warn_threshold: Decimal | None,
    fail_threshold: Decimal | None,
    critical_threshold: Decimal | None,
) -> tuple[str, Decimal | None]:
    """Resolve a check outcome to its persisted ``(status, metric_value)``."""
    if outcome.errored:
        return "error", None
    if outcome.skipped:
        return "skip", None
    metric = extract_metric(outcome)
    if outcome.severity is not None:
        return outcome.severity, metric
    status = derive_status(
        success=outcome.success,
        metric_value=metric,
        warn_threshold=warn_threshold,
        fail_threshold=fail_threshold,
        critical_threshold=critical_threshold,
    )
    return status, metric


def derive_status(
    *,
    success: bool,
    metric_value: Decimal | None,
    warn_threshold: Decimal | None,
    fail_threshold: Decimal | None,
    critical_threshold: Decimal | None,
) -> str:
    """Resolve a result's tier (`pass`/`warn`/`fail`/`critical`), ADR 0005."""
    no_thresholds = warn_threshold is None and fail_threshold is None and critical_threshold is None
    if no_thresholds or metric_value is None:
        return "pass" if success else "fail"

    if critical_threshold is not None and metric_value >= critical_threshold:
        return "critical"
    if fail_threshold is not None and metric_value >= fail_threshold:
        return "fail"
    if warn_threshold is not None and metric_value >= warn_threshold:
        return "warn"
    return "pass"
