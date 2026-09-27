"""Stakeholder-readable alert rendering (#2108)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from backend.app.alerting import email as email_mod
from backend.app.alerting import render
from backend.app.alerting.base import CheckReport, IncidentCard, RunReport


def _check(
    expectation_type: str = "expect_column_values_to_be_unique",
    *,
    status: str = "fail",
    expected: dict[str, Any] | None = None,
    observed: dict[str, Any] | None = None,
    sample: dict[str, Any] | None = None,
    metric: float | None = None,
) -> CheckReport:
    return CheckReport(
        check_name="order_id unique",
        expectation_type=expectation_type,
        status=status,
        metric_value=metric,
        observed_value=observed,
        expected_value=expected,
        sample_summary=sample,
    )


def _card(
    evidence: dict[str, Any] | None, *, is_new: bool = False, count: int = 54
) -> IncidentCard:
    return IncidentCard(
        incident_id=uuid.UUID("9451bf1d-0000-0000-0000-000000000000"),
        check_id=uuid.uuid4(),
        check_name="order_id unique",
        status="fail",
        occurrence_count=count,
        is_new=is_new,
        evidence=evidence,
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (99.4232987312572, "99.42"),
        (1486.8912, "1,486.89"),
        (34480, "34,480"),
        (10.0, "10"),
        (0.5, "0.5"),
        (0.004, "0"),
        (-2.456, "-2.46"),
    ],
)
def test_format_number_rounds_to_two_decimals(value: float, expected: str) -> None:
    assert render.format_number(value) == expected


def test_sample_note_and_detail_round_the_percentage() -> None:
    check = _check(
        expected={"column": "order_number"},
        sample={"unexpected_percent": 99.4232987312572, "unexpected_count": 34480},
        metric=99.4232987312572,
    )
    assert render.check_sample_note(check) == "99.42% unexpected"
    detail = render.check_detail(check)
    assert "99.42% unexpected" in detail and "observed 99.42" in detail
    assert "99.4232" not in detail


def test_unique_check_reads_as_a_sentence() -> None:
    check = _check(
        expected={"column": "order_number"},
        sample={"unexpected_percent": 99.4232987312572, "unexpected_count": 34480},
    )
    assert render.plain_check_summary(check) == (
        "34,480 rows (99.42%) have a duplicate order_number."
    )


def test_between_check_names_the_allowed_range() -> None:
    check = _check(
        "expect_column_values_to_be_between",
        expected={"column": "rating", "min_value": 1, "max_value": 5},
        sample={"unexpected_count": 1},
    )
    assert (
        render.plain_check_summary(check) == "1 row has rating outside the allowed range (1 to 5)."
    )


def test_freshness_reads_as_an_age_in_days_with_the_last_update() -> None:
    check = _check(
        "monitor:freshness",
        expected={"column": "order_ts", "monitor": "freshness"},
        observed={"age_hours": 1486.89, "max_timestamp": "2026-07-27T06:38:51+00:00"},
    )
    assert render.plain_check_summary(check) == (
        "The newest record is 61.95 days old (last updated 27 Jul 2026, 06:38 UTC)."
    )


def test_summary_never_invents_a_count_it_does_not_have() -> None:
    assert render.plain_check_summary(_check(expected={"column": "x"})) == ""


def test_error_and_skip_say_the_data_was_not_verified() -> None:
    assert "couldn't run" in render.plain_check_summary(_check(status="error"))
    assert "skipped" in render.plain_check_summary(_check(status="skip"))


def test_incident_facts_are_labelled_and_name_downstream_tables() -> None:
    evidence = {
        "upstream_pipeline_run": None,
        "sibling_checks": [{"status": "fail"}] + [{"status": "pass"}] * 6,
        "downstream_blast_radius": {
            "assets": [{"name": n} for n in ("orders_daily", "revenue", "churn", "ltv")],
            "qualified_by": [
                "warehouse lineage refresh failing on 'Unity Catalog — dataq_retail'",
                "warehouse lineage on 'probe-snowflake-dev' has not refreshed recently",
            ],
        },
    }
    facts = dict(render.incident_facts(_card(evidence)))
    assert facts["Incident"] == "9451bf1d — ongoing, failed on 54 runs so far."
    assert facts["Triggered by"] == "A manual or scheduled run (no upstream pipeline)."
    assert facts["Same run"] == "1 of 7 other checks also failed."
    assert facts["Downstream"] == (
        "May affect 4 downstream tables: orders_daily, revenue, churn and 1 more. "
        "Note: 2 lineage sources couldn't be refreshed, so this list may be incomplete "
        "or out of date."
    )


def test_a_suspended_prune_is_stated_as_extra_tables_not_missing_ones() -> None:
    evidence: dict[str, Any] = {
        "upstream_pipeline_run": None,
        "sibling_checks": [],
        "downstream_blast_radius": {
            "assets": [],
            "qualified_by": [
                "warehouse lineage on 'wh' has never pruned removed edges — edges below may "
                "include dependencies that no longer exist"
            ],
        },
    }
    downstream = dict(render.incident_facts(_card(evidence)))["Downstream"]
    assert "may also include tables that no longer depend on this one" in downstream
    assert "incomplete" not in downstream


def test_missing_evidence_is_stated_not_dropped() -> None:
    facts = dict(render.incident_facts(_card(None, is_new=True)))
    assert facts["Incident"] == "9451bf1d — new problem, first time this check has failed."
    assert facts["Context"] == "Not available for this incident."


def test_email_leads_with_the_plain_sentence_and_keeps_numbers_rounded() -> None:
    check = _check(
        expected={"column": "order_number"},
        sample={
            "unexpected_percent": 99.4232987312572,
            "unexpected_count": 34480,
            "partial_unexpected_list": ["ORD-00000161", "ORD-00000162", "ORD-00000163", "X"],
        },
    )
    report = RunReport(
        run_id=uuid.uuid4(),
        suite_id=uuid.uuid4(),
        suite_name="Snowflake — Orders",
        run_status="succeeded",
        datasource_type="snowflake",
        target_label="RETAIL.ORDERS_HEADER",
        worst_severity="fail",
        counts={"fail": 1},
        checks=[check],
        finished_at=None,
        incidents=[_card({"upstream_pipeline_run": None, "sibling_checks": []})],
    )
    text = email_mod.render_text_body(report)
    assert "found 1 problem that need attention" not in text
    assert "and found 1 problem that needs attention." in text
    assert "What we found: 34,480 rows (99.42%) have a duplicate order_number." in text
    assert "Examples: ORD-00000161, ORD-00000162, ORD-00000163, +1 more" in text
    html = email_mod.render_html_body(report)
    assert "34,480 rows (99.42%) have a duplicate order_number." in html
    assert "99.4232" not in html and "99.4232" not in text
