"""One safe-message policy for every sink that shows an exception (#595 C5/C6).

`safe_failure_reason` replaced three near-copies of the same isinstance branch —
the monitor loop had one, the run path grew a second (narrower) one, and the
dry-run preview had none at all. That last gap was the reported defect: a
`ScanTooLargeError` naming the file, the cap and the knob reached a real run, and
the preview of the very same target said "dry run could not execute".
"""

from __future__ import annotations

import pytest

from backend.app.core.errors import SafeMonitorError
from backend.app.datasources.monitors import MonitorConfigError
from backend.app.datasources.sampling import SamplingDrawError, ScanTooLargeError
from backend.app.services.failure_classifier import (
    classify_failure_reason,
    safe_failure_reason,
)


class _MarkedError(SafeMonitorError):
    pass


def test_a_safe_marked_message_survives_verbatim() -> None:
    assert safe_failure_reason(_MarkedError("column 'nope' is not in ORDERS")) == (
        "column 'nope' is not in ORDERS"
    )


@pytest.mark.parametrize(
    "exc",
    [
        ScanTooLargeError(
            "file 'x.csv' is 999 bytes, over the scan cap of 10 — RUN_MAX_SCAN_BYTES"
        ),
        SamplingDrawError("the random sample of 'orders' returned no rows from a table of 10,000"),
        MonitorConfigError("freshness column 'nope' is not a date/timestamp"),
    ],
)
def test_every_marked_error_this_feature_adds_keeps_its_remedy(
    exc: SafeMonitorError,
) -> None:
    """The point of the marker: these messages ARE the remedy. Classified, each
    becomes "the run failed to execute; see the server logs" — the undiagnosable
    outcome #755 already delivers when the worker is OOM-killed instead."""
    assert safe_failure_reason(exc) == str(exc)


def test_an_unmarked_driver_error_is_still_classified() -> None:
    """The contract stays narrow. A driver message can echo a DSN, a credential
    or a bound cell value, so it is read only to pick a category."""
    reason = safe_failure_reason(RuntimeError("login failed for user 'svc' at acct.example"))
    assert "svc" not in reason and "acct.example" not in reason
    assert reason == classify_failure_reason(RuntimeError("login failed"))


def test_a_marked_error_with_an_EMPTY_message_falls_back_to_classification() -> None:
    """A marked exception with nothing to say is worse than the generic sentence,
    not better — an empty `failure_reason` renders as no reason at all."""
    assert safe_failure_reason(_MarkedError("")) != ""
    assert safe_failure_reason(_MarkedError("")) == classify_failure_reason(_MarkedError(""))
