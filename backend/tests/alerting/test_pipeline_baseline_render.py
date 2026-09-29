"""Every channel renders a pipeline-baseline edge (#1653) through the workspace-signal path."""

from __future__ import annotations

import functools
import json

import pytest

from backend.app.alerting.base import HEALTH_FAILING, HEALTH_RECOVERED, PipelineBaselineReport
from backend.app.alerting.card import render_teams_signal_message
from backend.app.alerting.email import render_signal_subject, render_signal_text_body
from backend.app.alerting.slack import render_slack_signal_message

_report = functools.partial(
    PipelineBaselineReport, provider="airflow", pipeline_or_dag_id="load_orders", env="prod"
)
SLOW = _report(state=HEALTH_FAILING, duration_seconds=400, mean_duration_seconds=100, z_score=30.0)
FAST = _report(state=HEALTH_FAILING, duration_seconds=5, mean_duration_seconds=100, z_score=9.5)
OVERDUE = _report(state=HEALTH_FAILING, hours_since_last_success=72.0, overdue_threshold_hours=30.0)
RECOVERED = _report(state=HEALTH_RECOVERED)


@pytest.mark.parametrize(
    ("report", "headline"),
    [
        (SLOW, "pipeline ran much slower than usual: airflow load_orders (prod)"),
        (FAST, "pipeline ran much faster than usual: airflow load_orders (prod)"),
        (OVERDUE, "pipeline overdue: airflow load_orders (prod)"),
        (RECOVERED, "pipeline back to normal: airflow load_orders (prod)"),
    ],
)
def test_every_channel_carries_the_headline(report: PipelineBaselineReport, headline: str) -> None:
    assert headline in json.dumps(render_teams_signal_message(report))
    assert headline in json.dumps(render_slack_signal_message(report))
    assert headline in render_signal_subject(report)
    assert headline in render_signal_text_body(report)


def test_facts_say_how_far_out_and_what_it_usually_takes() -> None:
    body = render_signal_text_body(SLOW)
    assert "Usually takes" in body and "30.0 standard deviations" in body
    overdue = render_signal_text_body(OVERDUE)
    assert "Since last successful run" in overdue and "Expected within" in overdue
    assert "standard deviations" not in overdue
