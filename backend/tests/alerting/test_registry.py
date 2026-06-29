"""Tests for the publisher registry — type, caching, reset.

Delivery behaviour (per-suite config, webhook resolution, policy) is exercised
in the DB-backed `test_teams.py`; here we only assert what the registry returns.
"""

from __future__ import annotations

import uuid

from backend.app.alerting import registry
from backend.app.alerting.base import ResultPublisher, RunReport
from backend.app.alerting.composite import CompositePublisher
from backend.app.alerting.noop import NoopPublisher


def test_returns_composite_publisher() -> None:
    # The registry returns a composite over every channel (Teams · Slack · email);
    # each child self-no-ops per run when its channel is unconfigured (no secret /
    # recipients), notifications are disabled, or the run is below the threshold.
    assert isinstance(registry.get_result_publisher(), CompositePublisher)


def test_publishers_satisfy_the_protocol() -> None:
    # runtime_checkable Protocol — both impls are structural ResultPublishers.
    assert isinstance(registry.get_result_publisher(), ResultPublisher)
    assert isinstance(NoopPublisher(), ResultPublisher)


def test_publisher_is_cached() -> None:
    assert registry.get_result_publisher() is registry.get_result_publisher()


def test_reset_rebuilds() -> None:
    first = registry.get_result_publisher()
    registry.reset_result_publisher_cache()
    assert registry.get_result_publisher() is not first


def test_noop_publish_is_a_silent_drop() -> None:
    report = RunReport(
        run_id=uuid.uuid4(),
        suite_id=uuid.uuid4(),
        suite_name="s",
        run_status="failed",
        datasource_type="snowflake",
        target_label="T",
        worst_severity="fail",
        counts={"fail": 1},
        checks=[],
        finished_at=None,
    )
    # No channel → publishing must not raise; the session arg is ignored.
    NoopPublisher().publish(None, report)  # type: ignore[arg-type]  # returns None; must not raise
