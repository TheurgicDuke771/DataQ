"""Shared GX machinery for the datasource `CheckRunner`s."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import great_expectations as gx
import great_expectations.expectations as gxe

from backend.app.core.logging import get_logger
from backend.app.datasources.base import (
    SAMPLE_ROW_CAP,
    VALUE_SIGNAL_SUMMARY_KEY,
    CheckOutcome,
    CheckSpec,
    SuiteOutcome,
)
from backend.app.services.column_classification import value_signal_summary

log = get_logger(__name__)

# Failing-row keys copied into CheckOutcome.sample_failures. May contain real
# data — they reach logs / the read API only via the redactor (#415).
_SAMPLE_KEYS = (
    "partial_unexpected_list",
    "unexpected_count",
    "unexpected_percent",
    "unexpected_index_list",
)

# Cap on rows `_value_signal_summary_by_column` examines (#1230) — unbounded,
# the per-cell regex/entropy work is O(rows x columns) inside the Celery run path.
_VALUE_SIGNAL_SUMMARY_ROW_CAP = 5_000

# GX injects internal bookkeeping keys into kwargs at run time; strip them.
_GX_INTERNAL_KWARGS = frozenset({"batch_id"})

# Submission-position marker stamped into each expectation's `meta` (#767): GX 1.17 reorders results
# once any expectation errors, cross-wiring the positional zip.
_INDEX_META_KEY = "dataq_index"


# Connection types whose CheckRunner evaluates ordinary expectations on a SQL batch. Unity Catalog
# is deliberately absent: its pushdown set is an allowlist, so anything outside it — including
# every `DATAFRAME_ONLY_EXPECTATION_TYPES` entry — routes to that runner's pandas batch.
SQL_BATCH_CONNECTION_TYPES: frozenset[str] = frozenset({"snowflake"})


class UnknownExpectationError(ValueError):
    """Raised when a check's expectation_type has no matching GX expectation."""


def _expectation_class_name(expectation_type: str) -> str:
    """snake_case GX type → PascalCase class name."""
    return "".join(part.title() for part in expectation_type.split("_"))


def _to_gx_expectation(spec: CheckSpec, index: int | None = None) -> Any:
    """Build the concrete GX expectation for `spec`, stamping ``dataq_index``
    into ``meta`` when ``index`` is given (#767).

    Deliberately NOT gated on `expectation_allowlist` (#1510): that is an author-time gate, so a
    stored check whose type leaves the allowlist keeps running instead of erroring on every run.
    """
    class_name = _expectation_class_name(spec.expectation_type)
    expectation_cls = getattr(gxe, class_name, None)
    if expectation_cls is None:
        raise UnknownExpectationError(
            f"Unknown expectation_type {spec.expectation_type!r} (no gx class {class_name!r})"
        )
    if index is None:
        return expectation_cls(**spec.kwargs)
    kwargs = dict(spec.kwargs)
    caller_meta = kwargs.get("meta")
    if caller_meta is not None and not isinstance(caller_meta, dict):
        # A malformed stored `meta` (legacy row) must surface GX's own validation
        # error, not a bare dict() ValueError from the marker merge.
        return expectation_cls(**kwargs)
    meta = dict(caller_meta or {})
    meta[_INDEX_META_KEY] = index
    kwargs["meta"] = meta
    return expectation_cls(**kwargs)


def _is_identifier_index_list(value: Any) -> bool:
    """True for a non-empty list of row dicts; a plain COMPLETE run returns bare
    positional indices, which are not locators and are dropped.
    """
    return (
        isinstance(value, list) and len(value) > 0 and all(isinstance(row, dict) for row in value)
    )


def _value_signal_summary_by_column(rows: list[Any]) -> dict[str, dict[str, int]]:
    """Per-column `column_classification.value_signal_summary` over `rows` (#1230)."""
    bounded_rows = rows[:_VALUE_SIGNAL_SUMMARY_ROW_CAP]
    by_column: dict[str, list[Any]] = defaultdict(list)
    for row in bounded_rows:
        if isinstance(row, dict):
            for col, val in row.items():
                by_column[str(col)].append(val)
    summary: dict[str, dict[str, int]] = {}
    for col, values in by_column.items():
        col_summary = value_signal_summary(values)
        if col_summary is not None:
            summary[col] = col_summary
    return summary


def _extract_sample_failures(result: dict[str, Any]) -> dict[str, Any] | None:
    """Copy the failing-row keys out of a GX result, bounded to `SAMPLE_ROW_CAP` (#1196)."""
    sample: dict[str, Any] = {}
    for key in _SAMPLE_KEYS:
        if key not in result:
            continue
        value = result[key]
        if key == "unexpected_index_list" and not _is_identifier_index_list(value):
            continue
        if (
            key == "unexpected_index_list"
            and isinstance(value, list)
            and len(value) > SAMPLE_ROW_CAP
        ):
            summary = _value_signal_summary_by_column(value)
            if summary:
                sample[VALUE_SIGNAL_SUMMARY_KEY] = summary
        sample[key] = value[:SAMPLE_ROW_CAP] if isinstance(value, list) else value
    return sample or None


def _bounded_observed_value(detail: dict[str, Any]) -> dict[str, Any] | None:
    """Copy `observed_value` out of a GX result, bounded to `SAMPLE_ROW_CAP` (#1229)."""
    if "observed_value" not in detail:
        return None
    value = detail["observed_value"]
    if isinstance(value, list):
        value = value[:SAMPLE_ROW_CAP]
    return {"observed_value": value}


def _check_errored(exception_info: Any) -> tuple[bool, str | None]:
    """Did this expectation raise while being evaluated? (GX `exception_info`)."""
    if not isinstance(exception_info, dict) or not exception_info:
        return False, None
    if "raised_exception" in exception_info:  # flat shape
        return bool(exception_info.get("raised_exception")), exception_info.get("exception_message")
    # keyed-by-metric shape: errored if any metric computation raised
    for entry in exception_info.values():
        if isinstance(entry, dict) and entry.get("raised_exception"):
            return True, entry.get("exception_message")
    return False, None


def _expected_value(kwargs: Any) -> dict[str, Any] | None:
    cleaned = {key: value for key, value in dict(kwargs).items() if key not in _GX_INTERNAL_KWARGS}
    return cleaned or None


#: The exact message great_expectations' own `ExpectColumnValuesToBeOfType._validate`
#: raises when `column` is absent from the already-introspected `table.column_types`
#: metric: a bare `[...][0]` on an empty list, i.e. a plain `IndexError` with no
#: column name or context. GX successfully read the table's real columns — the crash
#: IS the signal this one isn't among them (#1850) — so this is narrowly rewritten
#: into the same "does not exist" wording the sibling map-type expectations already
#: get for free from a live SQL error, rather than patched in the vendored library.
_OF_TYPE_EXPECTATION = "expect_column_values_to_be_of_type"
_OF_TYPE_MISSING_COLUMN_MESSAGE = "list index out of range"


def _rewrite_of_type_missing_column(
    expectation_type: str, kwargs: Any, error_message: str | None
) -> str | None:
    if expectation_type != _OF_TYPE_EXPECTATION or error_message != _OF_TYPE_MISSING_COLUMN_MESSAGE:
        return error_message
    column = dict(kwargs).get("column") if kwargs else None
    if not isinstance(column, str) or not column:
        return error_message
    return f'the column "{column}" does not exist on this table'


def _submission_index(check_result: Any) -> int | None:
    """The ``dataq_index`` marker stamped into this result's expectation `meta`, or
    ``None`` when absent (a manually-constructed / legacy result carrying no marker).
    """
    config = getattr(check_result, "expectation_config", None)
    meta = getattr(config, "meta", None)
    if isinstance(meta, dict):
        index = meta.get(_INDEX_META_KEY)
        if isinstance(index, int) and not isinstance(index, bool):
            return index
    return None


def _in_submission_order(results: list[Any]) -> list[Any]:
    """Re-key GX results back to submission order via the `dataq_index` marker (#767)."""
    indexed: list[tuple[int, Any]] = []
    unmarked = 0
    for result in results:
        index = _submission_index(result)
        if index is None:
            unmarked += 1
        else:
            indexed.append((index, result))
    if unmarked:
        if indexed:
            # Every production expectation is stamped, so a *partial* marker loss is anomalous —
            # falling back silently would resurrect the #767 cross-wiring without a trace.
            log.warning(
                "gx_results_partially_unmarked",
                unmarked=unmarked,
                marked=len(indexed),
            )
        return results
    indexed.sort(key=lambda pair: pair[0])
    return [result for _, result in indexed]


def to_suite_outcome(gx_result: Any) -> SuiteOutcome:
    """Map a GX ExpectationSuiteValidationResult onto our GX-agnostic DTO."""
    outcomes: list[CheckOutcome] = []
    for check_result in _in_submission_order(list(gx_result.results)):
        config = check_result.expectation_config
        detail: dict[str, Any] = check_result.result or {}
        observed = _bounded_observed_value(detail)
        errored, error_message = _check_errored(getattr(check_result, "exception_info", None))
        if errored:
            error_message = _rewrite_of_type_missing_column(
                config.type, config.kwargs, error_message
            )
        outcomes.append(
            CheckOutcome(
                expectation_type=config.type,
                success=bool(check_result.success),
                observed_value=observed,
                expected_value=_expected_value(config.kwargs) if config.kwargs else None,
                sample_failures=_extract_sample_failures(detail),
                errored=errored,
                error_message=error_message,
            )
        )
    return SuiteOutcome(success=bool(gx_result.success), checks=outcomes)


def _execute(
    context: Any,
    *,
    batch_definition: Any,
    checks: list[CheckSpec],
    name: str,
    batch_parameters: dict[str, Any] | None,
    result_format: Any,
) -> SuiteOutcome:
    """Register the suite + validation definition (GX 1.x requires both on the
    ephemeral per-run context before ``run()``) and map the result.
    """
    suite = context.suites.add(
        gx.ExpectationSuite(
            name=name,
            expectations=[_to_gx_expectation(check, index=i) for i, check in enumerate(checks)],
        )
    )
    validation_definition = context.validation_definitions.add(
        gx.ValidationDefinition(name=f"vd-{name}", data=batch_definition, suite=suite)
    )
    result = validation_definition.run(
        batch_parameters=batch_parameters, result_format=result_format
    )
    return to_suite_outcome(result)


def run_expectations(
    context: Any,
    *,
    batch_definition: Any,
    checks: list[CheckSpec],
    name: str,
    batch_parameters: dict[str, Any] | None = None,
    index_columns: list[str] | None = None,
) -> SuiteOutcome:
    """Register the suite + validation definition for `batch_definition` and run."""
    if not index_columns:
        return _execute(
            context,
            batch_definition=batch_definition,
            checks=checks,
            name=name,
            batch_parameters=batch_parameters,
            result_format="COMPLETE",
        )
    outcome = _execute(
        context,
        batch_definition=batch_definition,
        checks=checks,
        name=name,
        batch_parameters=batch_parameters,
        result_format={"result_format": "COMPLETE", "unexpected_index_column_names": index_columns},
    )
    if outcome.checks and all(check.errored for check in outcome.checks):
        return _execute(
            context,
            batch_definition=batch_definition,
            checks=checks,
            name=f"{name}-noidx",
            batch_parameters=batch_parameters,
            result_format="COMPLETE",
        )
    return outcome
