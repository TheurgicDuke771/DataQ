"""Tests for the TeamsPublisher — webhook resolution, gating, the POST."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from backend.app.alerting import teams
from backend.app.alerting.base import CheckReport, RunReport
from backend.app.alerting.teams import TeamsPublisher


def _report(*, worst: str | None = "fail", run_status: str = "succeeded") -> RunReport:
    return RunReport(
        run_id=uuid.uuid4(),
        suite_id=uuid.uuid4(),
        suite_name="s",
        run_status=run_status,
        datasource_type="snowflake",
        target_label="T",
        worst_severity=worst,
        counts={"fail": 1} if worst else {"pass": 1},
        checks=[
            CheckReport(
                check_name="c",
                expectation_type="e",
                status=worst or "pass",
                metric_value=None,
                observed_value=None,
                expected_value=None,
                sample_summary=None,
            )
        ],
        finished_at=None,
    )


class _CapturePost:
    def __init__(self, *, status_code: int = 200) -> None:
        self.calls: list[dict[str, Any]] = []
        self._status_code = status_code

    def __call__(self, url: str, *, json: dict[str, Any], timeout: float) -> httpx.Response:
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return httpx.Response(self._status_code, request=httpx.Request("POST", url))


def test_posts_card_to_resolved_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    post = _CapturePost()
    monkeypatch.setattr(teams.httpx, "post", post)
    publisher = TeamsPublisher(lambda _r: "https://teams.example/hook")

    publisher.publish(_report(worst="fail"))

    assert len(post.calls) == 1
    call = post.calls[0]
    assert call["url"] == "https://teams.example/hook"
    assert call["json"]["type"] == "message"
    assert call["json"]["attachments"][0]["content"]["type"] == "AdaptiveCard"


def test_clean_run_does_not_send(monkeypatch: pytest.MonkeyPatch) -> None:
    post = _CapturePost()
    monkeypatch.setattr(teams.httpx, "post", post)
    TeamsPublisher(lambda _r: "https://teams.example/hook").publish(_report(worst=None))
    assert post.calls == []


def test_unresolved_webhook_does_not_send(monkeypatch: pytest.MonkeyPatch) -> None:
    post = _CapturePost()
    monkeypatch.setattr(teams.httpx, "post", post)
    TeamsPublisher(lambda _r: None).publish(_report(worst="fail"))
    assert post.calls == []


def test_http_error_propagates_for_dispatch_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    # A 4xx/5xx must raise so the dispatch layer (not the publisher) decides to
    # swallow it — the publisher is not allowed to silently eat a delivery failure.
    monkeypatch.setattr(teams.httpx, "post", _CapturePost(status_code=500))
    with pytest.raises(httpx.HTTPStatusError):
        TeamsPublisher(lambda _r: "https://teams.example/hook").publish(_report(worst="critical"))
