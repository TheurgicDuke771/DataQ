"""Tests for severity-aware routing — the send/urgency/escalation decision."""

from __future__ import annotations

import uuid

import pytest

from backend.app.alerting.base import RunReport
from backend.app.alerting.routing import CRITICAL, QUIET, STANDARD, route_for


def _report(*, worst: str | None, run_status: str = "succeeded") -> RunReport:
    return RunReport(
        run_id=uuid.uuid4(),
        suite_id=uuid.uuid4(),
        suite_name="s",
        run_status=run_status,
        datasource_type="snowflake",
        target_label="T",
        worst_severity=worst,
        counts={worst: 1} if worst else {"pass": 1},
        checks=[],
        finished_at=None,
    )


def test_critical_sends_and_escalates() -> None:
    route = route_for(_report(worst="critical"))
    assert route.should_send is True
    assert route.urgency == CRITICAL
    assert route.mention_channel is True


def test_fail_sends_standard_no_escalation() -> None:
    route = route_for(_report(worst="fail"))
    assert route.should_send is True
    assert route.urgency == STANDARD
    assert route.mention_channel is False


def test_warn_sends_quiet() -> None:
    route = route_for(_report(worst="warn"))
    assert route.should_send is True
    assert route.urgency == QUIET
    assert route.mention_channel is False


def test_clean_run_does_not_send() -> None:
    route = route_for(_report(worst=None))
    assert route.should_send is False
    assert route.mention_channel is False


def test_operational_failure_routes_standard() -> None:
    # A run that failed to execute (no result rows, no severity) is a real
    # failure — it must still alert, at standard urgency.
    route = route_for(_report(worst=None, run_status="failed"))
    assert route.should_send is True
    assert route.urgency == STANDARD


@pytest.mark.parametrize(
    ("worst", "run_status", "sends"),
    [
        ("critical", "succeeded", True),
        ("fail", "succeeded", True),
        ("warn", "succeeded", True),
        (None, "succeeded", False),
        (None, "failed", True),
    ],
)
def test_send_matrix(worst: str | None, run_status: str, sends: bool) -> None:
    assert route_for(_report(worst=worst, run_status=run_status)).should_send is sends
