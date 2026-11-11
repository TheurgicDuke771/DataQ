"""An alert names an orchestration provider the way the app does (#2354).

Every registered provider is exercised, at every alert text that names one and on every
channel that renders it, so a new provider or a new rendering site cannot fall back to
the raw identifier unnoticed.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from backend.app.alerting import email as email_mod
from backend.app.alerting import render
from backend.app.alerting.base import (
    HEALTH_FAILING,
    CheckReport,
    IncidentCard,
    PipelineBaselineReport,
    RunReport,
)
from backend.app.alerting.card import render_teams_message, render_teams_signal_message
from backend.app.alerting.routing import route_for
from backend.app.alerting.slack import render_slack_message, render_slack_signal_message
from backend.app.alerting.webhook import render_webhook_payload
from backend.app.orchestration.registry import (
    _PROVIDERS,
    provider_display_name,
    provider_display_names,
)
from backend.app.services.trigger_labels import PROVIDER_LABELS

PROVIDERS = sorted(_PROVIDERS)


def _run_report(triggered_by: str, incidents: list[IncidentCard] | None = None) -> RunReport:
    return RunReport(
        run_id=uuid.uuid4(),
        suite_id=uuid.uuid4(),
        suite_name="Orders",
        run_status="succeeded",
        datasource_type="snowflake",
        target_label="DB.S.ORDERS",
        worst_severity="fail",
        counts={"fail": 1},
        checks=[
            CheckReport("c", "expect_column_values_to_be_unique", "fail", None, None, None, None)
        ],
        finished_at=datetime.now(UTC),
        triggered_by=triggered_by,
        incidents=incidents or [],
    )


def _card(evidence: dict[str, Any]) -> IncidentCard:
    return IncidentCard(
        incident_id=uuid.uuid4(),
        check_id=uuid.uuid4(),
        check_name="c",
        status="open",
        occurrence_count=2,
        is_new=False,
        evidence=evidence,
        narrative=None,
    )


def test_adf_is_named_in_full() -> None:
    assert provider_display_name("adf") == "Azure Data Factory"


def test_an_unregistered_value_is_shown_as_given() -> None:
    assert provider_display_name("prefect") == "prefect"
    assert render.triggered_source("prefect:flow:run") == "prefect"


def test_run_labels_and_alerts_read_one_map() -> None:
    assert PROVIDER_LABELS == provider_display_names()
    for provider in PROVIDERS:
        assert render.triggered_source(f"{provider}:pl:run") == PROVIDER_LABELS[provider]


def test_non_provider_trigger_sources_keep_their_names() -> None:
    assert render.triggered_source("schedule:1") == "Schedule"
    assert render.triggered_source(None) == "Manual"


@pytest.mark.parametrize("provider", PROVIDERS)
def test_run_alert_names_the_provider_on_every_channel(provider: str) -> None:
    name = PROVIDER_LABELS[provider]
    report = _run_report(f"{provider}:pl_orders:run-1")
    route = route_for(report, "warn")
    expected = f"Triggered by: {name}"
    assert expected in email_mod.render_text_body(report)
    assert name in email_mod.render_html_body(report)
    slack = json.dumps(render_slack_message(report, route))
    assert name in slack
    teams = render_teams_message(report, route)
    assert {"title": "Triggered by", "value": name} in _facts(teams)


def _facts(node: Any) -> list[dict[str, Any]]:
    """Every FactSet fact in a Teams card, wherever it nests."""
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if node.get("type") == "FactSet":
            found.extend(node.get("facts", []))
        for value in node.values():
            found.extend(_facts(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_facts(item))
    return found


@pytest.mark.parametrize("provider", PROVIDERS)
def test_webhook_payload_keeps_the_identifier(provider: str) -> None:
    """The payload is read by machines: `triggered_by` stays the stored marker and the
    evidence keeps the provider value, whatever the display name is."""
    marker = f"{provider}:pl_orders:run-1"
    evidence: dict[str, Any] = {
        "upstream_pipeline_run": {"provider": provider, "status": "succeeded"}
    }
    payload = render_webhook_payload(_run_report(marker, [_card(evidence)]))
    assert payload["triggered_by"] == marker
    assert payload["incidents"][0]["evidence"]["upstream_pipeline_run"]["provider"] == provider


@pytest.mark.parametrize("provider", PROVIDERS)
def test_incident_evidence_names_the_provider(provider: str) -> None:
    name = PROVIDER_LABELS[provider]
    linked: dict[str, Any] = {
        "upstream_pipeline_run": {
            "provider": provider,
            "status": "succeeded",
            "delay_seconds_vs_history": 12.0,
        },
        "sibling_checks": [],
    }
    assert render._upstream_pipeline_clause(linked["upstream_pipeline_run"]) == (
        f"upstream {name} run succeeded (+12s vs history)"
    )
    assert dict(render.incident_facts(_card(linked)))["Triggered by"] == (
        f"{name} run (succeeded)."
    )
    unlinked: dict[str, Any] = {
        "upstream_pipeline_run": None,
        "pipeline_trigger": {
            "triggered": True,
            "provider": provider,
            "marker": f"{provider}:pl:r1",
            "pipeline_run": "not_recorded",
        },
        "sibling_checks": [],
    }
    assert dict(render.incident_facts(_card(unlinked)))["Triggered by"].startswith(
        f"The {name} pipeline ("
    )


@pytest.mark.parametrize("provider", PROVIDERS)
def test_pipeline_baseline_alert_names_the_provider_on_every_channel(provider: str) -> None:
    name = PROVIDER_LABELS[provider]
    report = PipelineBaselineReport(
        state=HEALTH_FAILING,
        provider=provider,
        pipeline_or_dag_id="load_orders",
        env="prod",
        hours_since_last_success=72.0,
        overdue_threshold_hours=30.0,
    )
    headline = f"pipeline overdue: {name} load_orders (prod)"
    teams = render_teams_signal_message(report)
    assert headline in json.dumps(teams)
    assert {"title": "Provider", "value": name} in _facts(teams)
    assert headline in json.dumps(render_slack_signal_message(report))
    assert headline in email_mod.render_signal_subject(report)
    body = email_mod.render_signal_text_body(report)
    assert headline in body and f"Provider: {name}" in body
    assert name in email_mod.render_signal_html_body(report)
