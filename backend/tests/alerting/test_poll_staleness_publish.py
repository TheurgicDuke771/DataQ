"""The `publish_poll_staleness` seam method (#1052) — composite semantics + renders.

The composite's contract here is deliberately STRONGER than its other two methods:
`workspace_health_service` records the #843 delivered-first flag based on whether this
call raised, so "every channel failed" must raise (else a total delivery failure would
be recorded as delivered and never retried), while one surviving channel must swallow
the rest (partial delivery counts, exactly like the per-connection edge).
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from sqlalchemy.orm import Session

from backend.app.alerting.base import (
    HEALTH_FAILING,
    HEALTH_RECOVERED,
    PollStalenessReport,
)
from backend.app.alerting.composite import CompositePublisher

_SESSION = cast(Session, None)  # the composite forwards it opaquely; no DB is touched


def _report(state: str = HEALTH_FAILING) -> PollStalenessReport:
    return PollStalenessReport(
        state=state,  # type: ignore[arg-type]  # tests pass the literal constants
        connection_count=3,
        most_recent_polled_at=None,
        threshold_seconds=1800,
    )


class _Channel:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.staleness_reports: list[PollStalenessReport] = []

    def publish(self, session: Any, report: Any) -> None: ...

    def publish_health(self, session: Any, report: Any) -> None: ...

    def publish_poll_staleness(self, session: Any, report: PollStalenessReport) -> None:
        if self.fail:
            raise RuntimeError("channel down")
        self.staleness_reports.append(report)


class TestCompositeStalenessContract:
    def test_fans_out_to_every_channel(self) -> None:
        channels = [_Channel(), _Channel()]
        CompositePublisher(channels).publish_poll_staleness(_SESSION, _report())
        assert all(len(c.staleness_reports) == 1 for c in channels)

    def test_one_broken_channel_does_not_stop_the_others_or_raise(self) -> None:
        broken, alive = _Channel(fail=True), _Channel()
        CompositePublisher([broken, alive]).publish_poll_staleness(_SESSION, _report())
        assert len(alive.staleness_reports) == 1

    def test_every_channel_failing_raises_so_the_flag_is_never_falsely_recorded(self) -> None:
        """The delivered-first hinge: if this silently returned, the caller would
        stamp `alerted_at` for an alert nobody received — and never retry."""
        with pytest.raises(RuntimeError):
            CompositePublisher([_Channel(fail=True), _Channel(fail=True)]).publish_poll_staleness(
                _SESSION, _report()
            )


class TestStalenessRenders:
    """Smoke over the shared render helpers: workspace-phrased, never connection-shaped."""

    def test_headline_is_workspace_phrased_both_edges(self) -> None:
        from backend.app.alerting import render

        assert "workspace-wide" in render.staleness_headline(_report(HEALTH_FAILING))
        assert "recovered" in render.staleness_headline(_report(HEALTH_RECOVERED))

    def test_facts_render_never_polled_honestly(self) -> None:
        from backend.app.alerting import render

        facts = dict(render.staleness_facts(_report()))
        assert facts["Most recent poll (any connection)"] == "never"
        assert facts["Orchestration connections"] == "3"

    def test_teams_slack_email_payloads_build(self) -> None:
        from backend.app.alerting.card import render_teams_staleness_message
        from backend.app.alerting.email import (
            render_staleness_html_body,
            render_staleness_subject,
            render_staleness_text_body,
        )
        from backend.app.alerting.slack import render_slack_staleness_message

        for state in (HEALTH_FAILING, HEALTH_RECOVERED):
            report = _report(state)
            assert render_teams_staleness_message(report)["attachments"]
            assert render_slack_staleness_message(report)["blocks"]
            assert render_staleness_subject(report).startswith("[DataQ]")
            assert "orchestration" in render_staleness_text_body(report).lower()
            assert "<table" in render_staleness_html_body(report)
