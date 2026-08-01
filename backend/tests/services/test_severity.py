"""Severity derivation unit tests (ADR 0016) — pure, no DB / no GX.

Covers `extract_metric` (GX unexpected-% → Decimal | None) and `derive_status`
(thresholds band the metric, higher = worse; thresholds override GX success;
binary fallback when no thresholds or no metric).
"""

from decimal import Decimal
from typing import Any

import pytest

from backend.app.datasources.base import CheckOutcome
from backend.app.services.severity import derive_status, extract_metric, resolve_status


def _outcome(sample: dict[str, Any] | None) -> CheckOutcome:
    return CheckOutcome(expectation_type="x", success=False, sample_failures=sample)


# ── extract_metric ──


def test_extract_metric_reads_unexpected_percent() -> None:
    assert extract_metric(_outcome({"unexpected_percent": 5.0})) == Decimal("5.0")


def test_extract_metric_zero_is_kept_not_treated_as_missing() -> None:
    # falsy 0 must survive — a clean check measures 0% unexpected, not "no metric"
    assert extract_metric(_outcome({"unexpected_percent": 0})) == Decimal("0")


def test_extract_metric_is_exact_decimal_from_float() -> None:
    # Decimal(str(0.5)) == 0.5 exactly, not the binary expansion
    assert extract_metric(_outcome({"unexpected_percent": 0.5})) == Decimal("0.5")


@pytest.mark.parametrize(
    "sample",
    [
        None,
        {},
        {"unexpected_count": 3},
        {"unexpected_percent": None},
        {"unexpected_percent": "nan?"},
    ],
)
def test_extract_metric_returns_none_when_absent_or_unparseable(
    sample: dict[str, Any] | None,
) -> None:
    assert extract_metric(_outcome(sample)) is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_extract_metric_rejects_non_finite(bad: float) -> None:
    # GX can yield NaN (empty table 0/0); Decimal(str(nan)) parses, so it must be
    # filtered or derive_status silently 'pass'es / raises mid-run.
    assert extract_metric(_outcome({"unexpected_percent": bad})) is None


# ── derive_status: binary fallback ──


@pytest.mark.parametrize(("success", "expected"), [(True, "pass"), (False, "fail")])
def test_no_thresholds_is_binary(success: bool, expected: str) -> None:
    assert (
        derive_status(
            success=success,
            metric_value=Decimal("99"),  # ignored when no thresholds set
            warn_threshold=None,
            fail_threshold=None,
            critical_threshold=None,
        )
        == expected
    )


@pytest.mark.parametrize(("success", "expected"), [(True, "pass"), (False, "fail")])
def test_thresholds_but_no_metric_is_binary(success: bool, expected: str) -> None:
    assert (
        derive_status(
            success=success,
            metric_value=None,  # aggregate check → no bandable metric
            warn_threshold=Decimal("1"),
            fail_threshold=Decimal("5"),
            critical_threshold=Decimal("20"),
        )
        == expected
    )


# ── derive_status: banding (warn=1, fail=5, critical=20) ──


@pytest.mark.parametrize(
    ("metric", "expected"),
    [
        ("0", "pass"),
        ("0.99", "pass"),
        ("1", "warn"),  # boundary is inclusive (>=)
        ("3", "warn"),
        ("5", "fail"),
        ("10", "fail"),
        ("20", "critical"),
        ("75", "critical"),
    ],
)
def test_full_threshold_banding(metric: str, expected: str) -> None:
    assert (
        derive_status(
            success=False,
            metric_value=Decimal(metric),
            warn_threshold=Decimal("1"),
            fail_threshold=Decimal("5"),
            critical_threshold=Decimal("20"),
        )
        == expected
    )


def test_thresholds_override_gx_success() -> None:
    # GX failed (success=False) but 0.5% is under the user's 1% warn → pass.
    assert (
        derive_status(
            success=False,
            metric_value=Decimal("0.5"),
            warn_threshold=Decimal("1"),
            fail_threshold=Decimal("5"),
            critical_threshold=Decimal("20"),
        )
        == "pass"
    )


@pytest.mark.parametrize(
    ("warn", "fail", "critical", "metric", "expected"),
    [
        (None, "5", None, "3", "pass"),  # only fail set
        (None, "5", None, "5", "fail"),
        ("1", None, None, "0.5", "pass"),  # only warn set
        ("1", None, None, "100", "warn"),  # no higher tier to escalate to
        (None, None, "20", "10", "pass"),  # only critical set
        (None, None, "20", "20", "critical"),
    ],
)
def test_partial_thresholds_skip_unset_tiers(
    warn: str | None, fail: str | None, critical: str | None, metric: str, expected: str
) -> None:
    assert (
        derive_status(
            success=False,
            metric_value=Decimal(metric),
            warn_threshold=Decimal(warn) if warn else None,
            fail_threshold=Decimal(fail) if fail else None,
            critical_threshold=Decimal(critical) if critical else None,
        )
        == expected
    )


# ── resolve_status: the operational statuses (#122 / #593) ──


def test_resolve_status_maps_an_errored_outcome_to_error() -> None:
    outcome = CheckOutcome("x", success=False, errored=True, metric_value=42.0)
    # Even with a metric present: an unevaluated check has no severity to band.
    assert resolve_status(
        outcome, warn_threshold=Decimal("1"), fail_threshold=None, critical_threshold=None
    ) == ("error", None)


def test_resolve_status_maps_a_skipped_outcome_to_skip() -> None:
    """#593 cold start. It lives in `resolve_status` rather than in the caller so
    the dry-run PREVIEW and the persisted run cannot disagree — the single reason
    this function exists."""
    outcome = CheckOutcome("monitor:anomaly", success=True, skipped=True)
    assert resolve_status(
        outcome, warn_threshold=None, fail_threshold=Decimal("3"), critical_threshold=None
    ) == ("skip", None)


def test_a_skipped_outcome_never_persists_a_metric() -> None:
    """A cold-start score would be a number computed from too little history;
    trending or baselining on it would launder a non-measurement into data."""
    outcome = CheckOutcome("monitor:anomaly", success=True, skipped=True, metric_value=99.0)
    status, metric = resolve_status(
        outcome, warn_threshold=None, fail_threshold=Decimal("3"), critical_threshold=None
    )
    assert (status, metric) == ("skip", None)


def test_errored_wins_over_skipped() -> None:
    """They are mutually exclusive by construction, but the order is pinned: a
    check that failed to evaluate must never be reported as merely skipped."""
    outcome = CheckOutcome("x", success=False, errored=True, skipped=True)
    assert resolve_status(
        outcome, warn_threshold=None, fail_threshold=None, critical_threshold=None
    ) == ("error", None)


def test_resolve_status_bands_a_monitor_metric_normally() -> None:
    """The control case: a non-operational outcome still goes through the ADR-0016
    banding, so the two early returns above cannot swallow the normal path."""
    outcome = CheckOutcome("monitor:anomaly", success=True, metric_value=4.5)
    assert resolve_status(
        outcome, warn_threshold=Decimal("2"), fail_threshold=Decimal("3"), critical_threshold=None
    ) == ("fail", Decimal("4.5"))
