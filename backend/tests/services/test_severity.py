"""Severity derivation unit tests (ADR 0016). Pure, no DB / no GX (`custom_sql`,
imported for its `CUSTOM_SQL_EXPECTATION_TYPE` constant, is FastAPI-error-shape-
only — no DB/GX import of its own).

Covers `extract_metric` (GX unexpected-% → Decimal | None, plus the custom-SQL
unexpected row COUNT fallback, #1202) and `derive_status` (thresholds band the
metric, higher = worse; thresholds override GX success; binary fallback when no
thresholds or no metric).
"""

from decimal import Decimal
from typing import Any

import pytest

from backend.app.datasources.base import CheckOutcome
from backend.app.services.custom_sql import CUSTOM_SQL_EXPECTATION_TYPE
from backend.app.services.severity import derive_status, extract_metric, resolve_status


def _outcome(sample: dict[str, Any] | None) -> CheckOutcome:
    return CheckOutcome(expectation_type="x", success=False, sample_failures=sample)


def _custom_sql_outcome(*, success: bool, observed_value: dict[str, Any] | None) -> CheckOutcome:
    return CheckOutcome(
        expectation_type=CUSTOM_SQL_EXPECTATION_TYPE,
        success=success,
        observed_value=observed_value,
    )


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


# ── extract_metric: custom-SQL row count fallback (ADR 0019, #1202) ──
# `UnexpectedRowsExpectation` has no `unexpected_percent`; its badness scalar is
# the unexpected row COUNT in `observed_value`. Datasource-agnostic by
# construction: `gx_runner.to_suite_outcome` produces this exact `CheckOutcome`
# shape for BOTH Snowflake (a plain SQL table batch) and Unity Catalog (the
# Databricks-SQL batch, ADR 0019 amendment) — see
# `test_gx_runner.py::test_to_suite_outcome_reads_custom_sql_row_count_as_observed_value`
# and `test_unity_catalog.py::test_custom_sql_row_count_feeds_severity_metric_value`
# for the two runner-level proofs that this function's input actually arrives in
# this shape from each datasource.


def test_extract_metric_reads_custom_sql_row_count() -> None:
    outcome = _custom_sql_outcome(success=False, observed_value={"observed_value": 74})
    assert extract_metric(outcome) == Decimal("74")


def test_extract_metric_custom_sql_zero_is_kept_not_treated_as_missing() -> None:
    # a passing custom-SQL check (0 unexpected rows) must measure 0, not "no metric"
    outcome = _custom_sql_outcome(success=True, observed_value={"observed_value": 0})
    assert extract_metric(outcome) == Decimal("0")


@pytest.mark.parametrize(
    "observed_value",
    [None, {}, {"observed_value": None}],
)
def test_extract_metric_custom_sql_returns_none_when_observed_value_absent(
    observed_value: dict[str, Any] | None,
) -> None:
    outcome = _custom_sql_outcome(success=False, observed_value=observed_value)
    assert extract_metric(outcome) is None


def test_extract_metric_does_not_read_observed_value_for_other_expectation_types() -> None:
    """The `observed_value` fallback is scoped to `unexpected_rows_expectation`
    ONLY. Plenty of ordinary GX expectations (e.g. `expect_table_row_count_to_equal`)
    also set `observed_value`, and their observed value is a measured fact, not a
    badness scalar — reading it here would silently start banding severity for
    every expectation type that happens to report one, well beyond this issue's
    scope."""
    outcome = CheckOutcome(
        expectation_type="expect_table_row_count_to_equal",
        success=True,
        observed_value={"observed_value": 42},
    )
    assert extract_metric(outcome) is None


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


# ── resolve_status: custom-SQL row count (ADR 0019, #1202) ──


def test_resolve_status_bands_a_custom_sql_check_with_thresholds() -> None:
    """A custom-SQL check WITH thresholds now bands on the unexpected row count,
    exactly like a monitor metric or an unexpected-percent metric."""
    outcome = _custom_sql_outcome(success=False, observed_value={"observed_value": 74})
    assert resolve_status(
        outcome,
        warn_threshold=Decimal("10"),
        fail_threshold=Decimal("50"),
        critical_threshold=Decimal("100"),
    ) == ("fail", Decimal("74"))


def test_resolve_status_custom_sql_without_thresholds_stays_binary_despite_populated_metric() -> (
    None
):
    """CRITICAL constraint: thresholds are optional, and populating `metric_value`
    must NOT turn today's binary (no-threshold) custom-SQL checks into something
    that bands unexpectedly. A no-threshold check with 74 unexpected rows must
    still resolve as a plain 'fail' (GX's own success/failure, ADR 0005's binary
    fallback) — not 'warn'/'fail'/'critical' from some implicit banding — even
    though `metric_value` is now populated and available for the trend view /
    anomaly baseline to read later."""
    outcome = _custom_sql_outcome(success=False, observed_value={"observed_value": 74})
    status, metric = resolve_status(
        outcome, warn_threshold=None, fail_threshold=None, critical_threshold=None
    )
    assert status == "fail"  # binary — not banded, despite metric_value now being set
    assert metric == Decimal("74")  # but the scalar IS persisted, for #594 / #593


def test_resolve_status_custom_sql_zero_rows_without_thresholds_is_plain_pass() -> None:
    outcome = _custom_sql_outcome(success=True, observed_value={"observed_value": 0})
    assert resolve_status(
        outcome, warn_threshold=None, fail_threshold=None, critical_threshold=None
    ) == ("pass", Decimal("0"))
