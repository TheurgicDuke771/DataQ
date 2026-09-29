"""Unit tests for the shared GX translation helpers."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.datasources.base import SAMPLE_ROW_CAP, CheckSpec
from backend.app.datasources.gx_runner import (
    _VALUE_SIGNAL_SUMMARY_ROW_CAP,
    _bounded_observed_value,
    _check_errored,
    _extract_sample_failures,
    _value_signal_summary_by_column,
    to_suite_outcome,
)
from backend.app.services.severity import extract_metric


def test_none_and_empty_are_not_errored() -> None:
    assert _check_errored(None) == (False, None)
    assert _check_errored({}) == (False, None)


def test_non_dict_is_not_errored() -> None:
    # GX types exception_info as Optional[dict]; a non-dict from a future shape / custom expectation
    # must NOT raise (it would flip the whole run to failed, discarding sibling results).
    assert _check_errored(["unexpected"]) == (False, None)
    assert _check_errored("oops") == (False, None)


def test_flat_shape_clean() -> None:
    info = {"raised_exception": False, "exception_message": None, "exception_traceback": None}
    assert _check_errored(info) == (False, None)


def test_flat_shape_raised() -> None:
    info = {"raised_exception": True, "exception_message": "boom", "exception_traceback": "..."}
    assert _check_errored(info) == (True, "boom")


def test_keyed_by_metric_shape_raised() -> None:
    info = {
        "MetricConfigurationID(metric_name='column_values.nonnull.condition', ...)": {
            "raised_exception": True,
            "exception_message": 'Error: The column "nope" in BatchData does not exist.',
            "exception_traceback": "Traceback ...",
        }
    }
    errored, message = _check_errored(info)
    assert errored is True
    assert message is not None and "nope" in message


def test_keyed_by_metric_shape_all_clean() -> None:
    # keyed entries that didn't raise (or lack the key) → not errored
    info = {
        "MetricConfigurationID(a)": {"raised_exception": False},
        "MetricConfigurationID(b)": {"exception_message": None},  # no raised_exception key
    }
    assert _check_errored(info) == (False, None)


# ── to_suite_outcome re-keys by the dataq_index marker (#767) ──


def _marked_result(*, index: int | None, type_: str, kwargs: dict[str, Any]) -> SimpleNamespace:
    """A GX-result stand-in carrying the `dataq_index` meta marker (or none)."""
    meta = {"dataq_index": index} if index is not None else {}
    return SimpleNamespace(
        success=True,
        expectation_config=SimpleNamespace(type=type_, kwargs=kwargs, meta=meta),
        result={},
    )


def test_to_suite_outcome_reorders_errored_first_gx_result() -> None:
    # Simulate GX's error-first ordering: submission was [A(0), B(1), C(2)] but GX returns the
    # errored B first.
    gx_result = SimpleNamespace(
        success=False,
        results=[
            _marked_result(index=1, type_="expect_b", kwargs={"column": "b"}),  # errored → first
            _marked_result(index=0, type_="expect_a", kwargs={"column": "a"}),
            _marked_result(index=2, type_="expect_c", kwargs={"column": "c"}),
        ],
    )
    outcome = to_suite_outcome(gx_result)
    assert [c.expectation_type for c in outcome.checks] == ["expect_a", "expect_b", "expect_c"]
    assert [c.expected_value for c in outcome.checks] == [
        {"column": "a"},
        {"column": "b"},
        {"column": "c"},
    ]


def test_to_suite_outcome_restores_authored_kwargs_by_submission_index() -> None:
    # #1618: GX returns the errored check first, so the authored spelling must follow the
    # dataq_index marker, not position — and only the key the runner rewrote is restored.
    folded = CheckSpec(
        "expect_compound_columns_to_be_unique",
        {"column_list": ["a", "b"], "mostly": 0.5},
        authored_kwargs={"column_list": ["A", "B"], "mostly": 0.5},
    )
    plain = CheckSpec("expect_x", {"column": "c"})
    gx_result = SimpleNamespace(
        success=False,
        results=[
            _marked_result(
                index=1,
                type_="expect_compound_columns_to_be_unique",
                kwargs={"column_list": ["a", "b"], "mostly": 0.50, "batch_id": "x"},
            ),
            _marked_result(index=0, type_="expect_x", kwargs={"column": "c"}),
        ],
    )
    first, second = to_suite_outcome(gx_result, [plain, folded]).checks
    assert first.expected_value == {"column": "c"}
    assert second.expected_value == {"column_list": ["A", "B"], "mostly": 0.5}


def test_to_suite_outcome_all_pass_preserves_order() -> None:
    gx_result = SimpleNamespace(
        success=True,
        results=[
            _marked_result(index=0, type_="expect_a", kwargs={}),
            _marked_result(index=1, type_="expect_b", kwargs={}),
        ],
    )
    outcome = to_suite_outcome(gx_result)
    assert [c.expectation_type for c in outcome.checks] == ["expect_a", "expect_b"]


def test_to_suite_outcome_reads_custom_sql_row_count_as_observed_value() -> None:
    """The shape `UnexpectedRowsExpectation` reports (ADR 0019): `observed_value` carries the
    unexpected row COUNT.
    """
    gx_result = SimpleNamespace(
        success=False,
        results=[
            SimpleNamespace(
                success=False,
                expectation_config=SimpleNamespace(
                    type="unexpected_rows_expectation",
                    kwargs={"unexpected_rows_query": "SELECT * FROM {batch} WHERE n > 0"},
                    meta={"dataq_index": 0},
                ),
                result={"observed_value": 74},
                exception_info=None,
            )
        ],
    )
    outcome = to_suite_outcome(gx_result)
    check = outcome.checks[0]
    assert check.observed_value == {"observed_value": 74}
    # The full round trip: what the runner produces is exactly what severity's
    # custom-SQL fallback needs (#1202) — no adapter code required in between.
    assert extract_metric(check) == Decimal("74")


def test_to_suite_outcome_without_markers_falls_back_to_gx_order() -> None:
    # Legacy / manually-constructed results (no meta marker) keep GX's list order —
    # backward-compatible with the existing constructed-result tests.
    gx_result = SimpleNamespace(
        success=True,
        results=[
            _marked_result(index=None, type_="expect_x", kwargs={}),
            _marked_result(index=None, type_="expect_y", kwargs={}),
        ],
    )
    outcome = to_suite_outcome(gx_result)
    assert [c.expectation_type for c in outcome.checks] == ["expect_x", "expect_y"]


def test_to_suite_outcome_partial_markers_falls_back_and_warns() -> None:
    # A *partial* marker loss is anomalous (every production expectation is stamped): keep GX's
    # order — never guess.
    from structlog.testing import capture_logs

    gx_result = SimpleNamespace(
        success=True,
        results=[
            _marked_result(index=1, type_="expect_x", kwargs={}),
            _marked_result(index=None, type_="expect_y", kwargs={}),
        ],
    )
    with capture_logs() as logs:
        outcome = to_suite_outcome(gx_result)
    assert [c.expectation_type for c in outcome.checks] == ["expect_x", "expect_y"]
    assert any(entry["event"] == "gx_results_partially_unmarked" for entry in logs)


def test_to_gx_expectation_non_dict_meta_surfaces_gx_error() -> None:
    # A malformed stored `meta` (legacy row) must produce GX's own validation error,
    # not a bare ValueError from the marker merge (dict("garbage")).
    import pytest

    from backend.app.datasources.base import CheckSpec
    from backend.app.datasources.gx_runner import _to_gx_expectation

    spec = CheckSpec(
        expectation_type="expect_column_values_to_not_be_null",
        kwargs={"column": "id", "meta": "garbage"},
    )
    with pytest.raises(Exception) as excinfo:
        _to_gx_expectation(spec, index=0)
    assert (
        not isinstance(excinfo.value, (ValueError, TypeError))
        or "validation" in str(excinfo.value).lower()
    ), f"expected GX's validation error, got bare {excinfo.value!r}"


# ── observed_value capture is bounded (#1229) ──


def test_bounded_observed_value_caps_a_set_oriented_expectations_list() -> None:
    # `expect_column_distinct_values_to_be_in_set` and siblings report the FULL observed
    # distinct-value set on every result format — no upper bound.
    values = [f"user{i}@example.com" for i in range(5_000)]
    observed = _bounded_observed_value({"observed_value": values})
    assert observed is not None
    assert len(observed["observed_value"]) == SAMPLE_ROW_CAP
    assert observed["observed_value"] == values[:SAMPLE_ROW_CAP]


def test_bounded_observed_value_leaves_a_short_list_untouched() -> None:
    values = ["a", "b", "c"]
    observed = _bounded_observed_value({"observed_value": values})
    assert observed == {"observed_value": values}


def test_bounded_observed_value_does_not_touch_a_scalar() -> None:
    # A row-count / mean / other aggregate metric must pass through completely
    # unchanged — only list/set-shaped values are in scope for the #1229 cap.
    observed = _bounded_observed_value({"observed_value": 74})
    assert observed == {"observed_value": 74}


def test_bounded_observed_value_absent_key_is_none() -> None:
    assert _bounded_observed_value({}) is None


def test_to_suite_outcome_caps_a_set_oriented_expectations_observed_value() -> None:
    # End-to-end through `to_suite_outcome`: a distinct-value-set expectation's
    # full result must come out bounded in the mapped `CheckOutcome`.
    values = [f"cust-{i}" for i in range(1_000)]
    gx_result = SimpleNamespace(
        success=False,
        results=[
            SimpleNamespace(
                success=False,
                expectation_config=SimpleNamespace(
                    type="expect_column_distinct_values_to_be_in_set",
                    kwargs={"column": "customer_ref", "value_set": ["a", "b"]},
                    meta={"dataq_index": 0},
                ),
                result={"observed_value": values},
                exception_info=None,
            )
        ],
    )
    outcome = to_suite_outcome(gx_result)
    check = outcome.checks[0]
    assert check.observed_value is not None
    assert len(check.observed_value["observed_value"]) == SAMPLE_ROW_CAP
    assert check.observed_value["observed_value"] == values[:SAMPLE_ROW_CAP]


# ── sample-failure capture is bounded (#1196) ──


# ── expect_column_values_to_be_of_type on a nonexistent column (#1850) ──


def _of_type_result(*, column: str, raised: bool) -> SimpleNamespace:
    """A GX result stand-in for `expect_column_values_to_be_of_type`, shaped exactly like the
    live crash: GX's own `_validate` raises a bare `IndexError("list index out of range")`
    when `column` is absent from its already-resolved `table.column_types` metric — no column
    name, no context, just the two words a plain Python list-index crash always carries.
    """
    exception_info = (
        {"raised_exception": True, "exception_message": "list index out of range"}
        if raised
        else {"raised_exception": False, "exception_message": None}
    )
    return SimpleNamespace(
        success=not raised,
        expectation_config=SimpleNamespace(
            type="expect_column_values_to_be_of_type",
            kwargs={"column": column, "type_": "string"},
            meta={"dataq_index": 0},
        ),
        result={},
        exception_info=exception_info,
    )


def test_of_type_missing_column_gets_an_actionable_message() -> None:
    gx_result = SimpleNamespace(
        success=False, results=[_of_type_result(column="nope_col", raised=True)]
    )
    outcome = to_suite_outcome(gx_result)
    check = outcome.checks[0]
    assert check.errored is True
    assert check.error_message == 'the column "nope_col" does not exist on this table'
    # The raw, uninformative library message must never leak through.
    assert "list index" not in check.error_message


def test_of_type_real_column_is_untouched_by_the_rewrite() -> None:
    gx_result = SimpleNamespace(
        success=True, results=[_of_type_result(column="channel", raised=False)]
    )
    outcome = to_suite_outcome(gx_result)
    check = outcome.checks[0]
    assert check.errored is False
    assert check.error_message is None


def test_of_type_rewrite_is_scoped_to_the_exact_known_message() -> None:
    """A DIFFERENT `IndexError` (or any other error) on the same expectation type must pass
    through unmodified — the rewrite is keyed on the exact, narrow signature of the known
    missing-column crash, not on the expectation type alone."""
    gx_result = SimpleNamespace(
        success=False,
        results=[
            SimpleNamespace(
                success=False,
                expectation_config=SimpleNamespace(
                    type="expect_column_values_to_be_of_type",
                    kwargs={"column": "channel", "type_": "string"},
                    meta={"dataq_index": 0},
                ),
                result={},
                exception_info={
                    "raised_exception": True,
                    "exception_message": "connection to warehouse timed out",
                },
            )
        ],
    )
    outcome = to_suite_outcome(gx_result)
    assert outcome.checks[0].error_message == "connection to warehouse timed out"


def test_of_type_rewrite_never_fires_for_other_expectation_types() -> None:
    """The identical library message from an unrelated expectation type (however
    unlikely) must not be reinterpreted as a missing-column error."""
    gx_result = SimpleNamespace(
        success=False,
        results=[
            SimpleNamespace(
                success=False,
                expectation_config=SimpleNamespace(
                    type="expect_column_values_to_match_regex",
                    kwargs={"column": "channel", "regex": "^a"},
                    meta={"dataq_index": 0},
                ),
                result={},
                exception_info={
                    "raised_exception": True,
                    "exception_message": "list index out of range",
                },
            )
        ],
    )
    outcome = to_suite_outcome(gx_result)
    assert outcome.checks[0].error_message == "list index out of range"


def test_extract_sample_failures_caps_row_lists() -> None:
    # A legacy / hand-built COMPLETE result hands back an untruncated `unexpected_index_list`.
    rows: list[Any] = [{"customer_id": i, "order_number": None} for i in range(5_000)]
    sample = _extract_sample_failures(
        {
            "unexpected_index_list": rows,
            "partial_unexpected_list": [None] * 5_000,
            "unexpected_count": 5_000,
            "unexpected_percent": 100.0,
        }
    )
    assert sample is not None
    assert len(sample["unexpected_index_list"]) == SAMPLE_ROW_CAP
    assert len(sample["partial_unexpected_list"]) == SAMPLE_ROW_CAP
    # the cap keeps the FIRST rows (a stable, deterministic sample) and never touches
    # the aggregate totals — the reader still learns the real failure count.
    assert sample["unexpected_index_list"] == rows[:SAMPLE_ROW_CAP]
    assert sample["unexpected_count"] == 5_000
    assert sample["unexpected_percent"] == 100.0


def test_extract_sample_failures_leaves_short_lists_untouched() -> None:
    rows = [{"customer_id": 1}, {"customer_id": 2}]
    sample = _extract_sample_failures({"unexpected_index_list": rows, "unexpected_count": 2})
    assert sample == {"unexpected_index_list": rows, "unexpected_count": 2}


# ── capture-time value-signal summary (#1230) ──


def test_extract_sample_failures_adds_a_value_signal_summary_when_truncating() -> None:
    # The #1196 cap narrows `unexpected_index_list` to SAMPLE_ROW_CAP rows for good — once
    # persisted, there is no larger list left to derive ratios from at read time.
    rows: list[Any] = [
        {
            "customer_email": (f"user{i}@example.com" if i < 3_000 else f"REF-{i}"),
            "qty": -i,
        }
        for i in range(5_000)
    ]
    sample = _extract_sample_failures({"unexpected_index_list": rows, "unexpected_count": 5_000})
    assert sample is not None
    summary = sample["value_signal_summary"]
    # counts reflect the FULL 5,000-row population, not the 20 emitted rows.
    assert summary["customer_email"] == {
        "n": 5_000,
        "email_count": 3_000,
        "id_shaped_count": 0,
        "encoded_count": 0,
        "distinct_count": 5_000,
    }
    assert summary["qty"]["n"] == 5_000
    # the summary rides alongside the still-capped rows, not instead of them.
    assert len(sample["unexpected_index_list"]) == SAMPLE_ROW_CAP


def test_extract_sample_failures_omits_the_summary_when_nothing_is_truncated() -> None:
    # Below the cap, the persisted rows already ARE the full population — a summary would be
    # redundant.
    rows = [{"customer_email": "a@x.com"}, {"customer_email": "b@x.com"}]
    sample = _extract_sample_failures({"unexpected_index_list": rows, "unexpected_count": 2})
    assert sample is not None
    assert "value_signal_summary" not in sample


def test_extract_sample_failures_omits_a_column_with_no_non_null_values() -> None:
    # A column that's NULL in every failing row (however many) has no value signal to persist.
    rows: list[Any] = [{"customer_email": None, "qty": -i} for i in range(5_000)]
    sample = _extract_sample_failures({"unexpected_index_list": rows, "unexpected_count": 5_000})
    assert sample is not None
    summary = sample["value_signal_summary"]
    assert "customer_email" not in summary
    assert summary["qty"]["n"] == 5_000


def test_value_signal_summary_by_column_groups_and_skips_non_dict_rows() -> None:
    rows: list[Any] = [
        {"a": "x@y.com", "b": 1},
        "not-a-row-dict",  # malformed row — must not raise, just be skipped
        {"a": "z@y.com", "b": 2},
    ]
    summary = _value_signal_summary_by_column(rows)
    assert summary["a"]["n"] == 2
    assert summary["a"]["email_count"] == 2
    assert summary["b"]["n"] == 2


def test_value_signal_summary_by_column_bounds_cpu_cost_on_a_huge_failing_population() -> None:
    """Review finding: an unbounded scan here means a badly-failing pandas-backed check
    (tens/hundreds of thousands of rows — #1196's own "thousands of failing rows" case, just
    moved from an O(1) truncation to O(rows) regex/entropy work) pays unbounded CPU
    synchronously in the Celery run path.
    """
    huge_row_count = _VALUE_SIGNAL_SUMMARY_ROW_CAP * 4
    rows: list[Any] = [{"col": f"v{i}@x.com"} for i in range(huge_row_count)]
    summary = _value_signal_summary_by_column(rows)
    assert summary["col"]["n"] == _VALUE_SIGNAL_SUMMARY_ROW_CAP  # bounded, not huge_row_count


def test_a_pandas_run_never_hashes_the_whole_frame(monkeypatch: Any) -> None:
    """GX's batch fingerprint hashes every column of the frame; a text column then costs ~13x its
    size in the worker (#2149). The run must never compute it."""
    import great_expectations as gx
    import pandas as pd
    from great_expectations.execution_engine import pandas_execution_engine

    from backend.app.datasources.gx_runner import run_expectations

    def _refuse(df: Any) -> str:
        raise AssertionError("the whole frame was hashed")

    monkeypatch.setattr(pandas_execution_engine, "hash_pandas_dataframe", _refuse)
    frame = pd.DataFrame({"id": [1, 2, 3], "note": ["a", "b", None]})
    context = gx.get_context(mode="ephemeral")
    batch_definition = (
        context.data_sources.add_pandas(name="p")
        .add_dataframe_asset(name="t")
        .add_batch_definition_whole_dataframe(name="w")
    )
    outcome = run_expectations(
        context,
        batch_definition=batch_definition,
        checks=[
            CheckSpec(
                expectation_type="expect_column_values_to_not_be_null", kwargs={"column": "id"}
            )
        ],
        name="s",
        batch_parameters={"dataframe": frame},
    )
    assert outcome.success is True


# ───────────────── one GX validation per process at a time (#2204) ─────────────────


def _not_null_run(context: Any, batch_definition: Any, frame: Any) -> Any:
    from backend.app.datasources.gx_runner import run_expectations

    return run_expectations(
        context,
        batch_definition=batch_definition,
        checks=[
            CheckSpec(
                expectation_type="expect_column_values_to_not_be_null", kwargs={"column": "id"}
            )
        ],
        name="s",
        batch_parameters={"dataframe": frame},
    )


def test_a_second_validation_cannot_install_its_context_mid_run() -> None:
    """GX resolves the datasource through its process-global project DURING validation, so a
    context installed by a concurrent run made the first run's lookups hit the wrong one.
    """
    import threading

    import pandas as pd

    from backend.app.datasources.gx_runner import ephemeral_gx_context

    frame = pd.DataFrame({"id": [1, 2, 3]})
    b_entered = threading.Event()
    b_done = threading.Event()

    def _run_b() -> None:
        with ephemeral_gx_context() as context:
            b_entered.set()
            context.data_sources.add_pandas(name="b")
        b_done.set()

    with ephemeral_gx_context() as context:
        batch_definition = (
            context.data_sources.add_pandas(name="a")
            .add_dataframe_asset(name="t")
            .add_batch_definition_whole_dataframe(name="w")
        )
        thread = threading.Thread(target=_run_b)
        thread.start()
        assert not b_entered.wait(timeout=0.5), "run B entered GX while run A held it"
        outcome = _not_null_run(context, batch_definition, frame)
    thread.join(timeout=10)

    assert outcome.checks[0].success is True
    assert b_done.is_set(), "run B proceeds once run A releases"


def test_a_validation_that_waits_too_long_is_refused_as_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    from backend.app.core.config import get_settings
    from backend.app.datasources.gx_runner import GxContextBusyError, ephemeral_gx_context

    monkeypatch.setenv("GX_CONTEXT_WAIT_SECONDS", "0.1")
    get_settings.cache_clear()
    errors: list[Exception] = []

    def _second() -> None:
        try:
            with ephemeral_gx_context():
                pass
        except Exception as exc:
            errors.append(exc)

    with ephemeral_gx_context():
        thread = threading.Thread(target=_second)
        thread.start()
        thread.join(timeout=10)

    assert len(errors) == 1 and isinstance(errors[0], GxContextBusyError)
    assert errors[0].status_code == 503


def test_a_failed_validation_releases_gx_for_the_next_one() -> None:
    from great_expectations.data_context.data_context.context_factory import project_manager

    from backend.app.datasources.gx_runner import _GX_PROJECT_LOCK, ephemeral_gx_context

    def _fail_mid_validation() -> None:
        with ephemeral_gx_context():
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        _fail_mid_validation()

    assert not _GX_PROJECT_LOCK.locked()
    assert getattr(project_manager, "_ProjectManager__project", None) is None
