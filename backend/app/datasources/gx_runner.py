"""Shared GX machinery for the datasource `CheckRunner`s."""

from __future__ import annotations

import numbers
import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal
from typing import Any

import great_expectations as gx
import great_expectations.expectations as gxe

from backend.app.core.logging import get_logger
from backend.app.datasources import gx_metrics  # noqa: F401  (registers the GX overrides)
from backend.app.datasources.base import (
    SAMPLE_ROW_CAP,
    VALUE_SIGNAL_SAMPLE_FAILED,
    VALUE_SIGNAL_STATUS_KEY,
    VALUE_SIGNAL_SUMMARY_KEY,
    CheckOutcome,
    CheckSpec,
    SuiteOutcome,
    ValueSignalGate,
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

_INDEX_LIST_KEY = "unexpected_index_list"
_PARTIAL_INDEX_LIST_KEY = "partial_unexpected_index_list"


def _sql_partial_unexpected_count() -> int:
    """The `partial_unexpected_count` the SQL lanes ask GX for (#1534).

    Clamped to GX's own `MAX_RESULT_RECORDS`: the values query clamps to it anyway
    (`min(partial_unexpected_count, MAX_RESULT_RECORDS)`) while the locator query does not,
    so an un-clamped cap above it would fetch locator rows the extractor then discards.
    """
    from great_expectations.constants import MAX_RESULT_RECORDS

    return min(SAMPLE_ROW_CAP, int(MAX_RESULT_RECORDS))


_SQL_PARTIAL_UNEXPECTED_COUNT = _sql_partial_unexpected_count()

# Cap on rows `_value_signal_summary_by_column` examines (#1230) — unbounded,
# the per-cell regex/entropy work is O(rows x columns) inside the Celery run path.
_VALUE_SIGNAL_SUMMARY_ROW_CAP = 5_000


def _frame_partial_unexpected_count() -> int:
    """The `partial_unexpected_count` the frame lanes ask GX for (#1995).

    The deepest read of the locator list is `_value_signal_summary_by_column`, not the
    `SAMPLE_ROW_CAP` sample — so anything below `_VALUE_SIGNAL_SUMMARY_ROW_CAP` would
    change the summary this cap exists to preserve. Deliberately NOT clamped to GX's
    `MAX_RESULT_RECORDS` the way the SQL cap is: on pandas that constant bounds only the
    unexpected-VALUES metric, and the locator metric — the one that costs — honours this
    number unclamped.
    """
    return max(SAMPLE_ROW_CAP, _VALUE_SIGNAL_SUMMARY_ROW_CAP)


_FRAME_PARTIAL_UNEXPECTED_COUNT = _frame_partial_unexpected_count()

# GX injects internal bookkeeping keys into kwargs at run time; strip them.
_GX_INTERNAL_KWARGS = frozenset({"batch_id"})

# Submission-position marker stamped into each expectation's `meta` (#767): GX 1.17 reorders results
# once any expectation errors, cross-wiring the positional zip.
_INDEX_META_KEY = "dataq_index"


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
    """True for a non-empty list of row dicts; with no identifier column configured GX
    returns bare positional indices, which are not locators and are dropped.
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


def _locator_rows(result: dict[str, Any]) -> tuple[bool, Any]:
    """The locator rows in a GX result, under whichever key this result format used.

    ``SUMMARY`` (every lane since #1534 / #1995) names them `partial_unexpected_index_list`;
    ``COMPLETE`` names the same rows, unbounded, as `unexpected_index_list`. Both are read so
    a hand-built or legacy COMPLETE result still maps.
    """
    for key in (_INDEX_LIST_KEY, _PARTIAL_INDEX_LIST_KEY):
        if key in result:
            return True, result[key]
    return False, None


def _label_value_signal(labels: Any, *, frame: Any, column: Any) -> dict[str, Any]:
    """The population summary for a frame lane with no identifier column (#2095).

    GX has already returned up to `_FRAME_PARTIAL_UNEXPECTED_COUNT` failing row LABELS; the
    tested column's values for them are read off the frame already in memory, so the frame lane
    classifies the same population the SQL lanes query for. A failed lookup keeps the capped
    sample and says so, never silently and never failing the run.
    """
    if not isinstance(labels, list) or len(labels) <= SAMPLE_ROW_CAP:
        return {}
    if not isinstance(column, str) or column not in frame.columns:
        return {}
    try:
        values = frame.loc[labels[:_VALUE_SIGNAL_SUMMARY_ROW_CAP], column].tolist()
        summary = _value_signal_summary_by_column([{column: value} for value in values])
    except Exception as exc:
        log.warning(
            "gx_value_signal_sample_failed", column_lookup=True, error_type=type(exc).__name__
        )
        return {VALUE_SIGNAL_STATUS_KEY: VALUE_SIGNAL_SAMPLE_FAILED}
    return {VALUE_SIGNAL_SUMMARY_KEY: summary} if summary else {}


def _extract_sample_failures(
    result: dict[str, Any], *, frame: Any = None, column: Any = None
) -> dict[str, Any] | None:
    """Copy the failing-row keys out of a GX result, bounded to `SAMPLE_ROW_CAP` (#1196).

    ``frame`` is the in-memory table on a frame lane (``None`` on the SQL lanes), used to read
    the tested ``column`` for the failing row labels GX returns when there is no identifier
    column (#2095).
    """
    sample: dict[str, Any] = {}
    for key in _SAMPLE_KEYS:
        if key == _INDEX_LIST_KEY:
            present, value = _locator_rows(result)
            if not present:
                continue
        elif key in result:
            value = result[key]
        else:
            continue
        if key == "unexpected_index_list" and not _is_identifier_index_list(value):
            if frame is not None:
                sample.update(_label_value_signal(value, frame=frame, column=column))
            continue
        # Fires on the FRAME lanes only: the SQL lanes' locator list is capped at
        # `_SQL_PARTIAL_UNEXPECTED_COUNT` (== SAMPLE_ROW_CAP, and GX's SQL locator query LIMITs
        # unconditionally), so it is never longer than the cap. The SQL lanes get their
        # population signal from a separate, gated, bounded query instead —
        # `_attach_population_signal` (#2014 — decision: option 2).
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


def _needs_population_signal(
    spec: CheckSpec,
    outcome: CheckOutcome,
    index_columns: list[str] | None,
    gate: ValueSignalGate,
) -> bool:
    """Would the SQL lane's capped sample decide masking where the frame lane uses the
    population (#2014)? Only for a column-map check with more unexpected rows than the capped
    sample holds — passing under `mostly` included, since its sample is persisted and shown
    just the same — and only when the gate says the value signal decides at least one of the
    columns the summary covers; policy/tags/name-decided columns never pay the query.
    """
    if outcome.errored:
        return False
    sample = outcome.sample_failures
    if not sample or VALUE_SIGNAL_SUMMARY_KEY in sample:
        return False
    count = sample.get("unexpected_count")
    # Any numeric the driver hands GX (int, numpy int, Decimal) — a type narrowing here would
    # silently skip the query on one warehouse only (#953).
    if isinstance(count, bool) or not isinstance(count, (numbers.Real, Decimal)):
        return False
    if count <= SAMPLE_ROW_CAP:
        return False
    column = spec.kwargs.get("column")
    if not isinstance(column, str) or not column:
        return False
    return any(gate(name) for name in (column, *(index_columns or ())))


def _population_metric(validator: Any, spec: CheckSpec, index_columns: list[str] | None) -> Any:
    """The metric whose value is the first `_VALUE_SIGNAL_SUMMARY_ROW_CAP` failing rows of ONE
    check, as locator dicts.

    GX's own `<map_metric>.unexpected_index_list`: the statement is GX's locator query — a
    SQLAlchemy Core ``SELECT <index cols>, <column> ... WHERE <GX's unexpected condition> LIMIT n``
    compiled by the connection's dialect — so it samples exactly the population the frame lane
    summarises, with no hand-built SQL or identifier quoting here. Only this metric's dependency
    graph runs (column reflection, a ``COUNT(*)``, the LIMITed select) — never the check's own
    unexpected-count scan again.
    """
    expectation = _to_gx_expectation(spec)
    map_metric = getattr(expectation, "map_metric", None)
    if not isinstance(map_metric, str):
        raise TypeError(f"{spec.expectation_type} is not a column-map expectation")
    result_format = {
        "result_format": "SUMMARY",
        "partial_unexpected_count": _VALUE_SIGNAL_SUMMARY_ROW_CAP,
        "unexpected_index_column_names": list(index_columns or ()),
    }
    dependencies = expectation.get_validation_dependencies(
        execution_engine=validator.execution_engine,
        runtime_configuration={"result_format": result_format},
    )
    metric = dependencies.get_metric_configuration(f"{map_metric}.unexpected_index_list")
    if metric is None:
        raise LookupError(f"{spec.expectation_type} has no unexpected_index_list metric")
    return metric


def _population_rows_batched(
    validator: Any, specs: dict[int, CheckSpec], index_columns: list[str] | None
) -> dict[int, Any]:
    """Every pending check's population rows in ONE metric-graph resolution (#2107).

    Resolved per check, each paid its own reflection, ``COUNT(*)`` and graph resolution — ~3 s a
    check on Snowflake / UC for ~0.2 s of warehouse work. Together, GX dedupes the shared
    dependencies and resolves the graph once.
    """
    metrics = {i: _population_metric(validator, spec, index_columns) for i, spec in specs.items()}
    # Keyed by metric id: `get_metrics` re-keys by metric NAME, which two checks can share.
    resolved, aborted = validator.compute_metrics(
        list(metrics.values()), runtime_configuration=None, min_graph_edges_pbar_enable=0
    )
    if aborted:
        raise RuntimeError(f"{len(aborted)} population metric(s) aborted")
    return {i: resolved[metric.id] for i, metric in metrics.items()}


def _batch_validator(batch_definition: Any, batch_parameters: dict[str, Any] | None) -> Any:
    from great_expectations.validator.validator import Validator

    batch = batch_definition.get_batch(batch_parameters=batch_parameters)
    return Validator(execution_engine=batch.data.execution_engine, batches=[batch])


def _attach_population_signal(
    outcome: SuiteOutcome,
    *,
    checks: list[CheckSpec],
    batch_definition: Any,
    batch_parameters: dict[str, Any] | None,
    index_columns: list[str] | None,
    gate: ValueSignalGate,
) -> SuiteOutcome:
    """Give the SQL lanes the `value_signal_summary` the frame lanes get for free (#2014).

    Decision (#2014, option 2): the locator query stays at `_SQL_PARTIAL_UNEXPECTED_COUNT`; a
    check whose masking the value signal would actually decide gets ONE extra bounded query.
    A failed query keeps today's capped-sample classification and says so — a
    `VALUE_SIGNAL_STATUS_KEY` marker on the persisted sample plus a WARNING — never silently.
    """
    pending = [
        i
        for i, (spec, check) in enumerate(zip(checks, outcome.checks, strict=True))
        if _needs_population_signal(spec, check, index_columns, gate)
    ]
    if not pending:
        return outcome
    updated = list(outcome.checks)
    started = time.monotonic()
    rows_by_check: dict[int, Any] = {}
    failed: dict[int, str] = {}
    try:
        validator = _batch_validator(batch_definition, batch_parameters)
        try:
            rows_by_check = _population_rows_batched(
                validator, {i: checks[i] for i in pending}, index_columns
            )
        except Exception:
            # One check's metric must not cost every other check its summary: retry singly.
            for i in pending:
                try:
                    rows_by_check.update(
                        _population_rows_batched(validator, {i: checks[i]}, index_columns)
                    )
                except Exception as exc:
                    failed[i] = type(exc).__name__
    except Exception as exc:
        failed = {i: type(exc).__name__ for i in pending}
    duration_ms = int((time.monotonic() - started) * 1000)
    for i in pending:
        spec, check = checks[i], updated[i]
        assert check.sample_failures is not None  # narrowed by `_needs_population_signal`
        if i in failed:
            log.warning(
                "gx_value_signal_sample_failed",
                expectation_type=spec.expectation_type,
                error_type=failed[i],
            )
            sample = {**check.sample_failures, VALUE_SIGNAL_STATUS_KEY: VALUE_SIGNAL_SAMPLE_FAILED}
        else:
            rows = rows_by_check.get(i)
            log.info(
                "gx_value_signal_sampled",
                expectation_type=spec.expectation_type,
                rows=len(rows) if isinstance(rows, list) else 0,
                limit=_VALUE_SIGNAL_SUMMARY_ROW_CAP,
                duration_ms=duration_ms,
                checks_in_batch=len(pending),
            )
            summary = _value_signal_summary_by_column(rows if isinstance(rows, list) else [])
            if not summary:
                continue
            sample = {**check.sample_failures, VALUE_SIGNAL_SUMMARY_KEY: summary}
        updated[i] = replace(check, sample_failures=sample)
    return SuiteOutcome(success=outcome.success, checks=updated)


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


def _expected_value(kwargs: Any, spec: CheckSpec | None = None) -> dict[str, Any] | None:
    cleaned = {key: value for key, value in dict(kwargs).items() if key not in _GX_INTERNAL_KWARGS}
    if spec is not None and spec.authored_kwargs is not None:
        # Report what the runner rewrote for the engine as authored (#1618); keys it left
        # alone keep GX's own rendering.
        for key, authored in spec.authored_kwargs.items():
            if key in cleaned and spec.kwargs.get(key) != authored:
                cleaned[key] = authored
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


def to_suite_outcome(
    gx_result: Any, checks: Sequence[CheckSpec] | None = None, *, frame: Any = None
) -> SuiteOutcome:
    """Map a GX ExpectationSuiteValidationResult onto our GX-agnostic DTO.

    ``checks`` are the submitted specs, matched to results by their ``dataq_index`` marker.
    """
    outcomes: list[CheckOutcome] = []
    for check_result in _in_submission_order(list(gx_result.results)):
        config = check_result.expectation_config
        index = _submission_index(check_result)
        spec = checks[index] if checks and index is not None and index < len(checks) else None
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
                expected_value=_expected_value(config.kwargs, spec) if config.kwargs else None,
                sample_failures=_extract_sample_failures(
                    detail, frame=frame, column=(config.kwargs or {}).get("column")
                ),
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
    frame = (batch_parameters or {}).get("dataframe")
    checks = [_date_bounds_as_dates(check, frame) for check in checks]
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
    return to_suite_outcome(result, checks, frame=frame)


_DATE_BOUND_KEYS = ("min_value", "max_value")


def _is_arrow_date(frame: Any, column: Any) -> bool:
    import pandas as pd
    import pyarrow as pa

    if frame is None or not isinstance(column, str) or column not in frame.columns:
        return False
    dtype = frame[column].dtype
    return isinstance(dtype, pd.ArrowDtype) and pa.types.is_date(dtype.pyarrow_dtype)


def _as_date(value: Any) -> Any:
    """A midnight datetime (or its ISO string) as the date it names; anything else unchanged."""
    import datetime as dt

    parsed = value
    if isinstance(value, str):
        try:
            parsed = dt.datetime.fromisoformat(value)
        except ValueError:
            return value
    if isinstance(parsed, dt.datetime) and parsed.tzinfo is None and parsed.time() == dt.time():
        return parsed.date().isoformat()
    return value


def _date_bounds_as_dates(check: CheckSpec, frame: Any) -> CheckSpec:
    """On a frame lane, a DATE column is an Arrow date, which a datetime bound cannot be compared
    with (#2175): a bound or set member naming a midnight becomes that date. Only what GX sees
    changes — the outcome keeps the authored kwargs."""
    kwargs = check.kwargs or {}
    if not _is_arrow_date(frame, kwargs.get("column")):
        return check
    changed = {key: _as_date(kwargs[key]) for key in _DATE_BOUND_KEYS if key in kwargs}
    if isinstance(kwargs.get("value_set"), list):
        changed["value_set"] = [_as_date(v) for v in kwargs["value_set"]]
    if changed == {key: kwargs[key] for key in changed}:
        return check
    return replace(
        check, kwargs={**kwargs, **changed}, authored_kwargs=check.authored_kwargs or kwargs
    )


def _is_sql_batch(batch_definition: Any) -> bool:
    """Does this batch definition run on a SQLAlchemy execution engine?

    Both lanes run SUMMARY; this only picks the cap. Undetermined resolves to False (the
    frame lane, the wider cap), but never silently: losing this attribute chain would fetch
    `_FRAME_PARTIAL_UNEXPECTED_COUNT` locator rows out of a warehouse instead of
    `_SQL_PARTIAL_UNEXPECTED_COUNT` — same sample, same totals, but a wider fetch and a
    `value_signal_summary` the SQL lanes never emit at their own cap.
    """
    from great_expectations.datasource.fluent import SQLDatasource

    try:
        datasource = batch_definition.data_asset.datasource
    except AttributeError:
        log.warning("gx_batch_lane_undetermined", batch=type(batch_definition).__name__)
        return False
    return isinstance(datasource, SQLDatasource)


def _result_format(*, sql_batch: bool, index_columns: list[str] | None) -> Any:
    """The GX result format for this lane: SUMMARY on both, at different caps.

    COMPLETE builds the whole failing set before anything is capped at capture (#1196), which
    bounds only what we persist. On a SQL engine that means the unexpected-VALUES query goes
    out with no ``LIMIT`` and the warehouse materialises every failing row (#1534). On a
    pandas batch it means GX builds a locator entry per failing row — a dict per row, via
    per-cell ``.at`` lookups, when an identifier column is configured — so a check failing on
    1M of 1M rows cost 22x the wall and +774 MiB of a passing one (#1995).

    SUMMARY pushes `partial_unexpected_count` onto the construction itself and returns the
    same leading rows under `partial_unexpected_index_list`.
    """
    result_format: dict[str, Any] = {
        "result_format": "SUMMARY",
        "partial_unexpected_count": (
            _SQL_PARTIAL_UNEXPECTED_COUNT if sql_batch else _FRAME_PARTIAL_UNEXPECTED_COUNT
        ),
    }
    if index_columns:
        result_format["unexpected_index_column_names"] = index_columns
    return result_format


def run_expectations(
    context: Any,
    *,
    batch_definition: Any,
    checks: list[CheckSpec],
    name: str,
    batch_parameters: dict[str, Any] | None = None,
    index_columns: list[str] | None = None,
    value_signal_gate: ValueSignalGate | None = None,
) -> SuiteOutcome:
    """Register the suite + validation definition for `batch_definition` and run.

    `value_signal_gate` (SQL lanes only, #2014) enables the bounded population sample for the
    checks whose masking the value signal would decide; ``None`` never issues it.
    """
    sql_batch = _is_sql_batch(batch_definition)
    outcome = _execute(
        context,
        batch_definition=batch_definition,
        checks=checks,
        name=name,
        batch_parameters=batch_parameters,
        result_format=_result_format(sql_batch=sql_batch, index_columns=index_columns or None),
    )
    used_index_columns = index_columns or None
    if used_index_columns and outcome.checks and all(check.errored for check in outcome.checks):
        used_index_columns = None
        outcome = _execute(
            context,
            batch_definition=batch_definition,
            checks=checks,
            name=f"{name}-noidx",
            batch_parameters=batch_parameters,
            result_format=_result_format(sql_batch=sql_batch, index_columns=None),
        )
    if not sql_batch or value_signal_gate is None:
        return outcome
    return _attach_population_signal(
        outcome,
        checks=checks,
        batch_definition=batch_definition,
        batch_parameters=batch_parameters,
        index_columns=used_index_columns,
        gate=value_signal_gate,
    )
