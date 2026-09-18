"""The failing-row list on a pandas batch is bounded where GX BUILDS it (#1995).

Real GX end to end on a pandas batch — the flat-file / Iceberg / Unity-Catalog-frame shape.
Under `COMPLETE` the pandas locator metric is constructed in full and only then sliced, so a
check failing on most of a large frame built one Python dict per failing row before the #1196
capture cap saw any of it. The cap the frame lanes now send is wide enough to leave the
`value_signal_summary` scan (#1230) reading exactly the rows it read before.
"""

from __future__ import annotations

import tracemalloc
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import great_expectations as gx
import numpy as np
import pandas as pd
import pytest

from backend.app.datasources import gx_runner
from backend.app.datasources.base import SAMPLE_ROW_CAP, VALUE_SIGNAL_SUMMARY_KEY, CheckSpec
from backend.app.datasources.gx_runner import (
    _FRAME_PARTIAL_UNEXPECTED_COUNT,
    _VALUE_SIGNAL_SUMMARY_ROW_CAP,
    _execute,
    _result_format,
    run_expectations,
)

_NOT_NULL = "expect_column_values_to_not_be_null"
_INDEX_COLUMNS = ["customer_id"]

#: One of each result-shape family: two map expectations (a row list), an aggregate (a scalar
#: `observed_value`), a distinct-value one (a list `observed_value`, #1229) and the frame-only
#: type check, which has no row list at all.
_CHECKS = [
    CheckSpec(_NOT_NULL, {"column": "order_number"}),
    CheckSpec("expect_column_values_to_be_between", {"column": "qty", "min_value": 0}),
    CheckSpec("expect_column_max_to_be_between", {"column": "qty", "min_value": 0, "max_value": 3}),
    CheckSpec(
        "expect_column_distinct_values_to_be_in_set", {"column": "sku", "value_set": ["SKU-OK"]}
    ),
    CheckSpec("expect_column_values_to_be_of_type", {"column": "customer_id", "type_": "int64"}),
]


def _frame(failing: int, *, arrow_backed: bool = False) -> pd.DataFrame:
    """A numpy-backed frame, or the Arrow-backed shape `read_parquet(dtype_backend="pyarrow")`
    and `iceberg._to_arrow_backed_pandas` hand the runner in production. GX's per-cell `.at`
    locator assembly takes a different path on `ArrowDtype` columns, and a numpy-only fixture
    would prove the bound only for the CSV/SQL-frame lanes (the #520 shape).
    """
    frame = pd.DataFrame(
        {
            "order_number": [None] * failing + ["OK"] * 7,
            "customer_id": np.arange(1000, 1000 + failing + 7),
            "qty": [-1] * failing + [1] * 7,
            "sku": [f"SKU-{i % 37}" for i in range(failing)] + ["SKU-OK"] * 7,
        }
    )
    return frame.convert_dtypes(dtype_backend="pyarrow") if arrow_backed else frame


def _frame_batch_definition(name: str) -> tuple[Any, Any]:
    context = gx.get_context(mode="ephemeral")
    asset = context.data_sources.add_pandas(name=f"p-{name}").add_dataframe_asset(name=f"t-{name}")
    return context, asset.add_batch_definition_whole_dataframe(name=f"wd-{name}")


def _legacy_complete(index_columns: list[str] | None) -> Any:
    """The result format the frame lanes sent before #1995."""
    if not index_columns:
        return "COMPLETE"
    return {"result_format": "COMPLETE", "unexpected_index_column_names": index_columns}


@contextmanager
def _raw_results(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    """The GX result dicts as GX built them, before `to_suite_outcome` maps and caps."""
    captured: list[dict[str, Any]] = []
    original = gx_runner.to_suite_outcome

    def spy(gx_result: Any) -> Any:
        captured.extend(check.result or {} for check in gx_result.results)
        return original(gx_result)

    monkeypatch.setattr(gx_runner, "to_suite_outcome", spy)
    yield captured


@pytest.mark.parametrize("arrow_backed", [False, True])
@pytest.mark.parametrize("index_columns", [None, _INDEX_COLUMNS])
@pytest.mark.parametrize("failing", [3, SAMPLE_ROW_CAP, 200, _VALUE_SIGNAL_SUMMARY_ROW_CAP + 1_000])
def test_frame_sample_output_is_unchanged(
    failing: int, index_columns: list[str] | None, arrow_backed: bool
) -> None:
    """Byte-compatibility across every result-shape family: the mapped outcome — sample keys,
    identifier locators (#415), the value-signal summary (#1230) and the bounded
    `observed_value` (#1229) — is identical to what the unbounded COMPLETE run produced, at,
    below and above both the sample cap and the summary scan cap.
    """
    frame = _frame(failing, arrow_backed=arrow_backed)
    context, batch_definition = _frame_batch_definition("new")
    bounded = run_expectations(
        context,
        batch_definition=batch_definition,
        checks=_CHECKS,
        name="new",
        batch_parameters={"dataframe": frame},
        index_columns=index_columns,
    )
    legacy_context, legacy_batch = _frame_batch_definition("legacy")
    legacy = _execute(
        legacy_context,
        batch_definition=legacy_batch,
        checks=_CHECKS,
        name="legacy",
        batch_parameters={"dataframe": frame},
        result_format=_legacy_complete(index_columns),
    )

    assert bounded == legacy


def test_the_value_signal_summary_still_reads_the_full_scan_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cap is set by the DEEPEST reader, not the sample: at `SAMPLE_ROW_CAP` the summary
    would be computed over 20 rows instead of `_VALUE_SIGNAL_SUMMARY_ROW_CAP`, which is a
    silent change to a persisted field rather than a crash.
    """
    failing = _VALUE_SIGNAL_SUMMARY_ROW_CAP + 1_000
    context, batch_definition = _frame_batch_definition("summary")
    with _raw_results(monkeypatch) as raw:
        outcome = run_expectations(
            context,
            batch_definition=batch_definition,
            checks=[CheckSpec(_NOT_NULL, {"column": "order_number"})],
            name="summary",
            batch_parameters={"dataframe": _frame(failing)},
            index_columns=_INDEX_COLUMNS,
        )

    assert len(raw[0]["partial_unexpected_index_list"]) >= _VALUE_SIGNAL_SUMMARY_ROW_CAP
    sample = outcome.checks[0].sample_failures
    assert sample is not None
    assert sample[VALUE_SIGNAL_SUMMARY_KEY]["customer_id"]["n"] == _VALUE_SIGNAL_SUMMARY_ROW_CAP


def test_the_frame_result_format_carries_the_cap() -> None:
    """The seam: what `_result_format` hands GX for a pandas batch. `COMPLETE` carries no
    bound at all, so this is the assertion that moves when the lane regresses.
    """
    for index_columns in (None, _INDEX_COLUMNS):
        result_format = _result_format(sql_batch=False, index_columns=index_columns)
        assert result_format["result_format"] == "SUMMARY"
        assert result_format["partial_unexpected_count"] == _FRAME_PARTIAL_UNEXPECTED_COUNT
    assert _FRAME_PARTIAL_UNEXPECTED_COUNT >= _VALUE_SIGNAL_SUMMARY_ROW_CAP


@pytest.mark.parametrize("arrow_backed", [False, True])
def test_a_widely_failing_frame_never_builds_a_row_per_failure(
    monkeypatch: pytest.MonkeyPatch, arrow_backed: bool
) -> None:
    """A check failing on every row of a 200k-row frame, with an identifier column so the
    locator entries are dicts. GX must never hand back a list proportional to the failure count.
    """
    rows = 200_000
    frame = _frame(rows, arrow_backed=arrow_backed)
    context, batch_definition = _frame_batch_definition("wide")

    with _raw_results(monkeypatch) as raw:
        tracemalloc.start()
        outcome = run_expectations(
            context,
            batch_definition=batch_definition,
            checks=[CheckSpec(_NOT_NULL, {"column": "order_number"})],
            name="wide",
            batch_parameters={"dataframe": frame},
            index_columns=_INDEX_COLUMNS,
        )
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

    result = raw[0]
    assert "unexpected_list" not in result
    assert "unexpected_index_list" not in result
    assert len(result["partial_unexpected_index_list"]) == _FRAME_PARTIAL_UNEXPECTED_COUNT

    # the bound trims what is BUILT, never the reported totals
    sample = outcome.checks[0].sample_failures
    assert sample is not None
    assert sample["unexpected_count"] == rows
    assert len(sample["unexpected_index_list"]) == SAMPLE_ROW_CAP

    # A ceiling, honestly: GX still assembles the full locator list inside
    # `compute_unexpected_pandas_indices` before `_pandas_map_condition_index` slices it, so
    # this bounds what is RETAINED, not every transient allocation. Measured on the development
    # rig at 215 MiB under COMPLETE against 57 MiB (numpy) / 52 MiB (Arrow) here; 120 sits
    # between the two regimes rather than on either.
    assert peak < 120 * 2**20, f"{peak / 2**20:.0f} MiB peaked during the run"
