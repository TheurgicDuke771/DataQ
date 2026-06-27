"""Tests for the publisher registry — no-op default, Teams wiring, caching."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from backend.app.alerting import registry, teams
from backend.app.alerting.base import CheckReport, ResultPublisher, RunReport
from backend.app.alerting.noop import NoopPublisher
from backend.app.alerting.teams import TeamsPublisher
from backend.app.core import secrets
from backend.app.core.config import get_settings


def _failing_report() -> RunReport:
    return RunReport(
        run_id=uuid.uuid4(),
        suite_id=uuid.uuid4(),
        suite_name="s",
        run_status="succeeded",
        datasource_type="snowflake",
        target_label="T",
        worst_severity="fail",
        counts={"fail": 1},
        checks=[
            CheckReport("c", "e", "fail", None, None, None, None),
        ],
        finished_at=None,
    )


def _configure_teams(monkeypatch: pytest.MonkeyPatch, *, webhook: str | None) -> None:
    """Point settings at a Teams webhook secret + (optionally) stock the secret.

    Forces the env secret store so the webhook is resolved from the patched
    ``KV_SECRET_*`` regardless of the ambient backend (a repo-root ``.env.app``
    may select redis). Caches are cleared so the next ``get_*`` rebuilds.
    """
    monkeypatch.setenv("SECRET_STORE", "env")
    monkeypatch.setenv("TEAMS_WEBHOOK_SECRET_NAME", "teams-webhook")
    if webhook is not None:
        monkeypatch.setenv("KV_SECRET_TEAMS_WEBHOOK", webhook)
    else:
        monkeypatch.delenv("KV_SECRET_TEAMS_WEBHOOK", raising=False)
    get_settings.cache_clear()
    secrets.reset_secret_store_cache()
    registry.reset_result_publisher_cache()


def test_default_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEAMS_WEBHOOK_SECRET_NAME", raising=False)
    get_settings.cache_clear()
    registry.reset_result_publisher_cache()
    assert isinstance(registry.get_result_publisher(), NoopPublisher)


def test_teams_publisher_when_webhook_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_teams(monkeypatch, webhook="https://teams.example/hook")
    assert isinstance(registry.get_result_publisher(), TeamsPublisher)


def test_configured_publisher_resolves_secret_and_posts(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_teams(monkeypatch, webhook="https://teams.example/hook")
    calls: list[str] = []

    def _post(url: str, *, json: dict[str, Any], timeout: float) -> httpx.Response:
        calls.append(url)
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(teams.httpx, "post", _post)
    registry.get_result_publisher().publish(_failing_report())
    assert calls == ["https://teams.example/hook"]


def test_configured_but_unresolved_secret_is_a_noop_send(monkeypatch: pytest.MonkeyPatch) -> None:
    # Name configured, but no secret value present → resolver returns None → no send.
    _configure_teams(monkeypatch, webhook=None)
    calls: list[str] = []

    def _post(url: str, **_: Any) -> httpx.Response:
        calls.append(url)
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(teams.httpx, "post", _post)
    registry.get_result_publisher().publish(_failing_report())
    assert calls == []


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
