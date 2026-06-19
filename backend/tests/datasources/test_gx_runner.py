"""Unit tests for the shared GX translation helpers.

`_check_errored` disambiguates the two `exception_info` shapes GX 1.17 emits per
expectation (a flat dict vs a dict keyed by `MetricConfigurationID`). The flat
`raised_exception: True` form isn't produced by the real-GX runner tests (a
missing column yields the keyed form), so it's covered directly here, alongside
the malformed / mixed payloads the run path must not crash on.
"""

from __future__ import annotations

from backend.app.datasources.gx_runner import _check_errored


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
