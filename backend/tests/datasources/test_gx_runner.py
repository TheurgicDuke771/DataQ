"""Unit tests for the shared GX translation helpers.

`_check_errored` disambiguates the two `exception_info` shapes GX 1.17 emits per
expectation (a flat dict vs a dict keyed by `MetricConfigurationID`). The flat
`raised_exception: True` form isn't produced by the real-GX runner tests (a
missing column yields the keyed form), so it's covered directly here, alongside
the malformed / mixed payloads the run path must not crash on.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from backend.app.datasources.gx_runner import _check_errored, to_suite_outcome


def test_none_and_empty_are_not_errored() -> None:
    assert _check_errored(None) == (False, None)
    assert _check_errored({}) == (False, None)


def test_non_dict_is_not_errored() -> None:
    # GX types exception_info as Optional[dict]; a non-dict from a future shape /
    # custom expectation must NOT raise (it would flip the whole run to failed,
    # discarding sibling results) — it's treated as "no error".
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
    # Simulate GX's error-first ordering: submission was [A(0), B(1), C(2)] but GX
    # returns the errored B first. The marker must restore submission order so each
    # outcome lands 1:1 on its submitted spec.
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
    # A *partial* marker loss is anomalous (every production expectation is stamped):
    # keep GX's order — never guess — but emit a warning so the fallback is visible
    # instead of silently resurrecting the #767 cross-wiring.
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
