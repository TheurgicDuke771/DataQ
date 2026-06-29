"""Wiring test for the `reap_stuck_runs` beat entry point (#309).

Pure-unit (no DB): the reaping behaviour is covered DB-backed in
`tests/services/test_run_reaper.py`. Here we only assert the task reads the
configured threshold, delegates to the service, returns the reaped count, and
always closes its session. The task deliberately does NOT publish alerts (see
`run_service.reap_stuck_runs`).
"""

from types import SimpleNamespace
from typing import Any

from backend.app.services import run_service
from backend.app.worker import tasks


def test_reaper_task_passes_threshold_returns_count_and_closes_session(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class _Session:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class _Settings:
        stuck_run_threshold_minutes = 75

    session = _Session()
    reaped = [SimpleNamespace(), SimpleNamespace()]

    monkeypatch.setattr(tasks, "get_session", lambda: session)
    monkeypatch.setattr(tasks, "get_settings", lambda: _Settings())

    def _capture(_session: Any, *, threshold_minutes: int) -> list[Any]:
        captured["session"] = _session
        captured["threshold_minutes"] = threshold_minutes
        return reaped

    monkeypatch.setattr(run_service, "reap_stuck_runs", _capture)

    assert tasks.reap_stuck_runs() == 2
    assert captured["session"] is session
    assert captured["threshold_minutes"] == 75
    assert session.closed is True
