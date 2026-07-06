"""Tests for the Teams Adaptive Card renderer (pure — no I/O)."""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from backend.app.alerting import card
from backend.app.alerting.base import CheckReport, RunReport
from backend.app.alerting.routing import route_for


def _content(report: RunReport) -> dict[str, Any]:
    """Render a report through its computed route and return the card content."""
    msg = card.render_teams_message(report, route_for(report))
    return cast(dict[str, Any], msg["attachments"][0]["content"])


def _report(
    *,
    checks: list[CheckReport] | None = None,
    counts: dict[str, int] | None = None,
    run_status: str = "succeeded",
    worst: str | None = "fail",
) -> RunReport:
    return RunReport(
        run_id=uuid.uuid4(),
        suite_id=uuid.uuid4(),
        suite_name="Orders QA",
        run_status=run_status,
        datasource_type="snowflake",
        target_label="RETAIL.ORDERS",
        worst_severity=worst,
        counts=counts or {"pass": 1, "fail": 1},
        checks=checks or [],
        finished_at=datetime(2026, 6, 26, 12, 0, tzinfo=UTC),
    )


def _check(status: str = "fail", **kw: object) -> CheckReport:
    base: dict[str, object] = {
        "check_name": "not-null id",
        "expectation_type": "expect_column_values_to_not_be_null",
        "status": status,
        "metric_value": 12.5,
        "observed_value": {"unexpected_percent": 12.5},
        "expected_value": {"column": "id"},
        "sample_summary": {"unexpected_count": 3},
    }
    base.update(kw)
    return CheckReport(**base)  # type: ignore[arg-type]


def _texts(content: dict[str, Any]) -> list[str]:
    return [b.get("text", "") for b in content["body"] if b["type"] == "TextBlock"]


def test_message_wraps_an_adaptive_card() -> None:
    report = _report()
    msg = card.render_teams_message(report, route_for(report))
    assert msg["type"] == "message"
    (attachment,) = msg["attachments"]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    content = attachment["content"]
    assert content["type"] == "AdaptiveCard"
    assert content["version"] == "1.4"


def test_card_carries_datasource_target_and_severity() -> None:
    factset = next(b for b in _content(_report())["body"] if b["type"] == "FactSet")
    facts = {f["title"]: f["value"] for f in factset["facts"]}
    assert facts["Datasource"] == "snowflake"
    assert facts["Target"] == "RETAIL.ORDERS"
    assert facts["Severity"] == "fail"
    # Metadata (from the shared render.run_metadata) — always at least the trigger.
    assert facts["Triggered by"] == "Manual"
    assert "Finished" not in facts  # replaced by Started/Duration metadata (#661)


def test_card_carries_owner_metadata_and_view_run_action() -> None:
    # #661/#416 parity: owner + env + a "View run" deep-link action on the card.
    report = dataclasses.replace(
        _report(),
        owner="Ada Lovelace",
        env="prod",
        run_url="https://dataq.example.com/results/abc",
    )
    content = _content(report)
    factset = next(b for b in content["body"] if b["type"] == "FactSet")
    facts = {f["title"]: f["value"] for f in factset["facts"]}
    assert facts["Owner"] == "Ada Lovelace"
    assert facts["Environment"] == "prod"
    assert content["actions"] == [
        {
            "type": "Action.OpenUrl",
            "title": "View run",
            "url": "https://dataq.example.com/results/abc",
        }
    ]


def test_card_omits_action_without_run_url() -> None:
    assert "actions" not in _content(_report())


def test_card_lists_failing_checks_with_observed_vs_expected() -> None:
    texts = _texts(_content(_report(checks=[_check(status="fail"), _check(status="pass")])))
    blob = "\n".join(texts)
    # The failing check is rendered; the passing one is not listed.
    assert "not-null id" in blob
    assert "observed unexpected_percent=12.5" in blob
    assert "expected column=id" in blob
    assert "3 rows" in blob  # the redacted sample count
    # Exactly one failing-check block (the passing check is excluded).
    assert sum("not-null id" in t for t in texts) == 1


def test_operational_failure_has_no_check_tally() -> None:
    texts = _texts(_content(_report(run_status="failed", checks=[], counts={}, worst=None)))
    assert any("Run failed to execute" in t for t in texts)


def test_check_overflow_is_summarised() -> None:
    texts = _texts(_content(_report(checks=[_check() for _ in range(13)])))
    # 10 rows rendered + a "+3 more".
    assert sum("not-null id" in t for t in texts) == 10
    assert any("+3 more" in t for t in texts)


def test_critical_adds_channel_escalation_banner() -> None:
    texts = _texts(_content(_report(worst="critical", counts={"critical": 1})))
    assert any("@channel" in t and "CRITICAL" in t for t in texts)


def test_warn_is_quiet_no_banner_and_amber_title() -> None:
    content = _content(_report(worst="warn", counts={"warn": 1}))
    texts = _texts(content)
    assert not any("@channel" in t for t in texts)
    title = content["body"][0]  # no banner → title is first
    assert title["text"] == "Orders QA"
    assert title["color"] == "warning"  # amber, calm


def test_fail_has_no_banner_and_red_title() -> None:
    content = _content(_report(worst="fail"))
    assert not any("@channel" in t for t in _texts(content))
    assert content["body"][0]["color"] == "attention"  # red


def test_clean_run_renders_as_a_success_card() -> None:
    # An 'always'-policy heartbeat on an all-pass run reads positive.
    content = _content(_report(worst=None, counts={"pass": 3}))
    title = content["body"][0]
    assert title["text"] == "Orders QA"
    assert title["color"] == "good"  # green
    assert any("All 3 checks passed" in t for t in _texts(content))
    assert not any("@channel" in t for t in _texts(content))


def test_compact_handles_empty() -> None:
    assert card._compact(None) == "—"
    assert card._compact({}) == "—"
    assert card._compact({"a": 1, "b": 2}) == "a=1, b=2"
