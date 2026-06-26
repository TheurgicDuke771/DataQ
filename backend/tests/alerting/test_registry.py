"""Tests for the publisher registry — no-op default, caching, reset."""

from __future__ import annotations

import uuid

from backend.app.alerting import registry
from backend.app.alerting.base import ResultPublisher, RunReport
from backend.app.alerting.noop import NoopPublisher


def test_default_is_noop() -> None:
    assert isinstance(registry.get_result_publisher(), NoopPublisher)


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
    # No channel configured → publishing must not raise or return anything.
    assert NoopPublisher().publish(report) is None


def test_noop_satisfies_the_protocol() -> None:
    # runtime_checkable Protocol — the no-op is a structural ResultPublisher.
    assert isinstance(NoopPublisher(), ResultPublisher)


def test_publisher_is_cached() -> None:
    assert registry.get_result_publisher() is registry.get_result_publisher()


def test_reset_rebuilds() -> None:
    first = registry.get_result_publisher()
    registry.reset_result_publisher_cache()
    assert registry.get_result_publisher() is not first
