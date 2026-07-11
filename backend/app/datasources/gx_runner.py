"""Shared Great Expectations machinery for the datasource `CheckRunner`s.

The GX-version-specific translation — snake_case `expectation_type` → GX class,
and GX `ExpectationSuiteValidationResult` → our GX-agnostic `SuiteOutcome` DTOs —
is identical across datasources. It lives here so every runner (Snowflake table,
flat-file DataFrame, Unity Catalog later) reuses one implementation and the GX v1
API is pinned in a single place (CLAUDE.md §5 — GX has drifted across releases).

Each runner only differs in how it builds the GX *batch* (a Snowflake table
asset vs an in-memory pandas asset); once it has a `batch_definition` it calls
`run_expectations`, which registers the suite + validation definition and maps
the result. Tests exercise these pure parts with constructed GX results and a
canned DataFrame — no live datasource required.
"""

from __future__ import annotations

from typing import Any

import great_expectations as gx
import great_expectations.expectations as gxe

from backend.app.datasources.base import CheckOutcome, CheckSpec, SuiteOutcome

# GX result keys that describe failing rows — copied into CheckOutcome.sample_failures.
# These may contain real data, so they only ever reach logs / the read API via the
# redactor. `unexpected_index_list` is the per-row list carrying the configured
# identifier column(s) + the failing value — populated only when `index_columns` is
# requested (#415); it makes a failing row *locatable*.
_SAMPLE_KEYS = (
    "partial_unexpected_list",
    "unexpected_count",
    "unexpected_percent",
    "unexpected_index_list",
)

# GX injects internal bookkeeping keys into expectation_config.kwargs at run time
# (e.g. batch_id); strip them so expected_value persists only the check's own
# parameters.
_GX_INTERNAL_KWARGS = frozenset({"batch_id"})

# The submission-position marker stamped into each expectation's `meta` so a result
# can be re-keyed to the `CheckSpec` it came from — GX 1.17 `graph_validate` returns
# results in a DIFFERENT order once any expectation errors (errored ones are appended
# first, then the rest in submission order), so the run-service's positional zip onto
# `checks` would otherwise cross-wire result rows to the wrong `check_id` (#767). GX
# carries `expectation_config.meta` through verbatim to every result, so it survives
# the reorder; it lives in `meta` (not `kwargs`), so it never leaks into `expected_value`.
_INDEX_META_KEY = "dataq_index"


class UnknownExpectationError(ValueError):
    """Raised when a check's expectation_type has no matching GX expectation."""


def _expectation_class_name(expectation_type: str) -> str:
    """snake_case GX type → PascalCase class name.

    ``expect_column_values_to_not_be_null`` → ``ExpectColumnValuesToNotBeNull``.
    """
    return "".join(part.title() for part in expectation_type.split("_"))


def _to_gx_expectation(spec: CheckSpec, index: int | None = None) -> Any:
    """Build the concrete GX expectation for `spec`.

    When ``index`` is given, stamp the check's submission position into the
    expectation's ``meta`` (``dataq_index``) so `to_suite_outcome` can re-key the
    result back to its spec regardless of the order GX returns results in (#767).
    Merged with any caller-supplied ``meta`` in ``kwargs``.
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
    meta = dict(kwargs.get("meta") or {})
    meta[_INDEX_META_KEY] = index
    kwargs["meta"] = meta
    return expectation_cls(**kwargs)


def _is_identifier_index_list(value: Any) -> bool:
    """A useful `unexpected_index_list` is a non-empty list of **row dicts** (the
    identifier columns + failing value, from `unexpected_index_column_names`). A plain
    COMPLETE run instead returns bare positional indices (``[1, 4, …]``) — not a
    locator, so we drop those to keep the sample clean."""
    return (
        isinstance(value, list) and len(value) > 0 and all(isinstance(row, dict) for row in value)
    )


def _extract_sample_failures(result: dict[str, Any]) -> dict[str, Any] | None:
    sample: dict[str, Any] = {}
    for key in _SAMPLE_KEYS:
        if key not in result:
            continue
        if key == "unexpected_index_list" and not _is_identifier_index_list(result[key]):
            continue
        sample[key] = result[key]
    return sample or None


def _check_errored(exception_info: Any) -> tuple[bool, str | None]:
    """Did this expectation raise while being evaluated? (GX `exception_info`).

    GX (1.17) reports `exception_info` in two shapes per `ExpectationValidationResult`:

    * a **flat** dict ``{'raised_exception': bool, 'exception_message': str|None,
      ...}`` for a cleanly-evaluated expectation, and
    * a dict **keyed by `MetricConfigurationID`** when a metric computation raised,
      each value being a flat dict with its own ``raised_exception``.

    Treat the check as errored if the flat form raised, or any keyed entry did;
    return the first exception message for debuggability. An errored check is an
    ``error`` result (#122), not a data ``fail``.
    """
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


def _submission_index(check_result: Any) -> int | None:
    """The ``dataq_index`` marker stamped into this result's expectation `meta`, or
    ``None`` when absent (a manually-constructed / legacy result carrying no marker)."""
    config = getattr(check_result, "expectation_config", None)
    meta = getattr(config, "meta", None)
    if isinstance(meta, dict):
        index = meta.get(_INDEX_META_KEY)
        if isinstance(index, int) and not isinstance(index, bool):
            return index
    return None


def _in_submission_order(results: list[Any]) -> list[Any]:
    """Re-key GX results back to submission order via the `dataq_index` marker (#767).

    GX 1.17 `graph_validate` returns results in submission order *only* while nothing
    errors; once any expectation errors it appends the errored ones first, so trusting
    list order cross-wires results to the wrong check. Sorting by the stamped index
    restores the 1:1 positional contract the run-service relies on. Falls back to GX's
    order if *any* result lacks the marker (constructed results in unit tests), so the
    legacy no-marker path is unchanged.
    """
    indexed: list[tuple[int, Any]] = []
    for result in results:
        index = _submission_index(result)
        if index is None:
            return results
        indexed.append((index, result))
    indexed.sort(key=lambda pair: pair[0])
    return [result for _, result in indexed]


def to_suite_outcome(gx_result: Any) -> SuiteOutcome:
    """Map a GX ExpectationSuiteValidationResult onto our GX-agnostic DTO.

    Results are re-keyed to submission order via the `dataq_index` marker (#767)
    before mapping, so the outcome list stays 1:1 with the submitted `CheckSpec`s
    even when GX reorders errored expectations to the front.

    Kept GX-translation-only (no datasource specifics) so it is unit-testable
    with a constructed GX result, no live datasource required.
    """
    outcomes: list[CheckOutcome] = []
    for check_result in _in_submission_order(list(gx_result.results)):
        config = check_result.expectation_config
        detail: dict[str, Any] = check_result.result or {}
        observed = (
            {"observed_value": detail["observed_value"]} if "observed_value" in detail else None
        )
        errored, error_message = _check_errored(getattr(check_result, "exception_info", None))
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
    ephemeral per-run context before ``run()``) and map the result."""
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
    """Register the suite + validation definition for `batch_definition` and run.

    The shared tail of every runner. `batch_parameters` carries the in-memory
    DataFrame for a pandas asset; it stays `None` for an asset that resolves its own
    batch (a SQL table), matching a bare ``run()``.

    ``index_columns`` (#415) requests GX's ``unexpected_index_column_names`` so each
    failing row is returned as a dict carrying those identifier column(s) + the failing
    value — the locator the redactor can surface. GX evaluates the index metric per
    expectation, so a **bad/absent** identifier column errors *every* check; we detect
    that (all checks errored, only when an index was requested) and fall back to a plain
    run, so the checks still evaluate — just without the row identifier.
    """
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
