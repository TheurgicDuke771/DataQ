"""Wiring test for the `reap_stuck_llm_invocations` beat entry point (#1644)."""

from types import SimpleNamespace
from typing import Any

from backend.app.services import llm_service
from backend.app.worker import tasks


def test_reaper_task_passes_thresholds_returns_count_and_closes_session(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class _Session:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class _Settings:
        llm_invocation_pending_threshold_minutes = 15
        llm_invocation_running_threshold_minutes = 10

    session = _Session()
    reaped = [SimpleNamespace(), SimpleNamespace(), SimpleNamespace()]

    monkeypatch.setattr(tasks, "get_session", lambda: session)
    monkeypatch.setattr(tasks, "get_settings", lambda: _Settings())

    def _capture(
        _session: Any, *, pending_threshold_minutes: int, running_threshold_minutes: int
    ) -> list[Any]:
        captured["session"] = _session
        captured["pending_threshold_minutes"] = pending_threshold_minutes
        captured["running_threshold_minutes"] = running_threshold_minutes
        return reaped

    monkeypatch.setattr(llm_service, "reap_stuck_invocations", _capture)

    assert tasks.reap_stuck_llm_invocations() == 3
    assert captured["session"] is session
    assert captured["pending_threshold_minutes"] == 15
    assert captured["running_threshold_minutes"] == 10
    assert session.closed is True
