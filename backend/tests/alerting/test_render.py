"""Tests for the shared Slack/email render helpers (#416).

Pure functions — no DB, no network — so they exercise the formatting branches
(expected-vs-observed, metric fallback, redacted sample, metadata, truncation)
directly on constructed DTOs.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from backend.app.alerting import render
from backend.app.alerting.base import CheckReport, RunReport


def _check(
    *,
    status: str = "fail",
    metric: float | None = None,
    observed: dict[str, Any] | None = None,
    expected: dict[str, Any] | None = None,
    sample: dict[str, Any] | None = None,
) -> CheckReport:
    return CheckReport("c", "expect_x", status, metric, observed, expected, sample)


def _report(**overrides: object) -> RunReport:
    base = {
        "run_id": uuid.uuid4(),
        "suite_id": uuid.uuid4(),
        "suite_name": "S",
        "run_status": "succeeded",
        "datasource_type": "snowflake",
        "target_label": "T",
        "worst_severity": "fail",
        "counts": {"fail": 1},
        "checks": [],
        "finished_at": None,
    }
    base.update(overrides)
    return RunReport(**base)  # type: ignore[arg-type]


# ── check_sample_note ─────────────────────────────────────────────────────────


def test_sample_note_prefers_percent_then_count() -> None:
    assert render.check_sample_note(_check(sample={"unexpected_percent": 3.2})) == "3.2% unexpected"
    assert render.check_sample_note(_check(sample={"unexpected_count": 5})) == "5 unexpected"
    # A falsy zero count must still render (not be dropped as "missing").
    assert render.check_sample_note(_check(sample={"unexpected_count": 0})) == "0 unexpected"
    assert render.check_sample_note(_check(sample=None)) == ""


# ── check_detail ──────────────────────────────────────────────────────────────


def test_detail_combines_expected_observed_and_sample() -> None:
    detail = render.check_detail(
        _check(
            expected={"min_value": 0, "column": "unit_price"},
            observed={"observed_value": 12},
            sample={"unexpected_percent": 3.2},
        )
    )
    assert detail == "expected min_value=0, column=unit_price · observed 12 · 3.2% unexpected"


def test_detail_falls_back_to_metric_value_when_no_observed_dict() -> None:
    assert render.check_detail(_check(metric=42.0)) == "observed 42"


def test_detail_unwraps_observed_value_single_key() -> None:
    assert render.check_detail(_check(observed={"observed_value": 7})) == "observed 7"


def test_detail_truncates_long_scalars() -> None:
    detail = render.check_detail(_check(expected={"value_set": "x" * 200}))
    assert detail.endswith("…")
    assert len(detail) < 120


def test_detail_empty_when_nothing_present() -> None:
    assert render.check_detail(_check()) == ""


def test_detail_previews_redacted_sample_values() -> None:
    detail = render.check_detail(
        _check(sample={"unexpected_count": 4, "partial_unexpected_list": [-5, -12, -3, -1]})
    )
    assert detail == "4 unexpected · e.g. -5, -12, -3, +1 more"


def test_sample_values_ignores_row_dicts_and_missing() -> None:
    # Row-dict samples are too wide for a one-liner (shown on the run page instead).
    assert render.check_sample_values(_check(sample={"partial_unexpected_list": [{"a": 1}]})) == ""
    assert render.check_sample_values(_check(sample=None)) == ""


# ── triggered_source ──────────────────────────────────────────────────────────


def test_triggered_source_maps_known_providers() -> None:
    assert render.triggered_source("schedule:123") == "Schedule"
    assert render.triggered_source("adf:pl:run1") == "ADF"
    assert render.triggered_source("airflow:dag:run") == "Airflow"
    assert render.triggered_source("dbt:job:run") == "dbt"
    assert render.triggered_source(None) == "Manual"
    assert render.triggered_source("weird:x") == "weird"  # unknown prefix → raw


# ── format_duration ───────────────────────────────────────────────────────────


def test_format_duration() -> None:
    assert render.format_duration(None) is None
    assert render.format_duration(4.25) == "4.2s"
    assert render.format_duration(125) == "2m 5s"


# ── run_metadata ──────────────────────────────────────────────────────────────


def test_run_metadata_includes_set_fields_and_omits_missing() -> None:
    started = datetime(2026, 7, 6, 4, 30, tzinfo=UTC)
    finished = datetime(2026, 7, 6, 4, 32, 5, tzinfo=UTC)
    pairs = dict(
        render.run_metadata(
            _report(env="prod", triggered_by="adf:pl:run", started_at=started, finished_at=finished)
        )
    )
    assert pairs["Environment"] == "prod"
    assert pairs["Triggered by"] == "ADF"
    assert pairs["Duration"] == "2m 5s"
    assert "2026-07-06 04:30" in pairs["Started"]


def test_run_metadata_omits_absent_env_and_duration() -> None:
    # No env, no timestamps → only the always-present trigger source (Manual).
    pairs = dict(render.run_metadata(_report()))
    assert pairs == {"Triggered by": "Manual"}
