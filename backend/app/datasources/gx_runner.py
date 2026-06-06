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
# These may contain real data, so they only ever reach logs via the redactor.
_SAMPLE_KEYS = ("partial_unexpected_list", "unexpected_count", "unexpected_percent")

# GX injects internal bookkeeping keys into expectation_config.kwargs at run time
# (e.g. batch_id); strip them so expected_value persists only the check's own
# parameters.
_GX_INTERNAL_KWARGS = frozenset({"batch_id"})


class UnknownExpectationError(ValueError):
    """Raised when a check's expectation_type has no matching GX expectation."""


def _expectation_class_name(expectation_type: str) -> str:
    """snake_case GX type → PascalCase class name.

    ``expect_column_values_to_not_be_null`` → ``ExpectColumnValuesToNotBeNull``.
    """
    return "".join(part.title() for part in expectation_type.split("_"))


def _to_gx_expectation(spec: CheckSpec) -> Any:
    class_name = _expectation_class_name(spec.expectation_type)
    expectation_cls = getattr(gxe, class_name, None)
    if expectation_cls is None:
        raise UnknownExpectationError(
            f"Unknown expectation_type {spec.expectation_type!r} (no gx class {class_name!r})"
        )
    return expectation_cls(**spec.kwargs)


def _extract_sample_failures(result: dict[str, Any]) -> dict[str, Any] | None:
    sample = {key: result[key] for key in _SAMPLE_KEYS if key in result}
    return sample or None


def _expected_value(kwargs: Any) -> dict[str, Any] | None:
    cleaned = {key: value for key, value in dict(kwargs).items() if key not in _GX_INTERNAL_KWARGS}
    return cleaned or None


def to_suite_outcome(gx_result: Any) -> SuiteOutcome:
    """Map a GX ExpectationSuiteValidationResult onto our GX-agnostic DTO.

    Kept GX-translation-only (no datasource specifics) so it is unit-testable
    with a constructed GX result, no live datasource required.
    """
    outcomes: list[CheckOutcome] = []
    for check_result in gx_result.results:
        config = check_result.expectation_config
        detail: dict[str, Any] = check_result.result or {}
        observed = (
            {"observed_value": detail["observed_value"]} if "observed_value" in detail else None
        )
        outcomes.append(
            CheckOutcome(
                expectation_type=config.type,
                success=bool(check_result.success),
                observed_value=observed,
                expected_value=_expected_value(config.kwargs) if config.kwargs else None,
                sample_failures=_extract_sample_failures(detail),
            )
        )
    return SuiteOutcome(success=bool(gx_result.success), checks=outcomes)


def run_expectations(
    context: Any,
    *,
    batch_definition: Any,
    checks: list[CheckSpec],
    name: str,
    batch_parameters: dict[str, Any] | None = None,
) -> SuiteOutcome:
    """Register the suite + validation definition for `batch_definition` and run.

    The shared tail of every runner: GX 1.x requires the suite and validation
    definition to be registered on the (ephemeral, per-run) context before
    ``run()``. `batch_parameters` carries the in-memory DataFrame for a pandas
    asset; it stays `None` for an asset that resolves its own batch (a SQL table),
    matching a bare ``run()``.
    """
    suite = context.suites.add(
        gx.ExpectationSuite(
            name=name,
            expectations=[_to_gx_expectation(check) for check in checks],
        )
    )
    validation_definition = context.validation_definitions.add(
        gx.ValidationDefinition(name=f"vd-{name}", data=batch_definition, suite=suite)
    )
    result = validation_definition.run(batch_parameters=batch_parameters, result_format="COMPLETE")
    return to_suite_outcome(result)
