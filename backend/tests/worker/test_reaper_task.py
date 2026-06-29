"""Wiring test for the `reap_stuck_runs` beat entry point (#309).

Pure-unit (no DB): the reaping behaviour is covered DB-backed in
`tests/services/test_run_reaper.py`. Here we only assert the task reads the
configured threshold, delegates to the service, publishes an operational-failure
alert per reaped run, and always closes its session.
"""

import uuid
from types import SimpleNamespace
from typing import Any

from backend.app.alerting import dispatch as alert_dispatch
from backend.app.services import run_service
from backend.app.worker import tasks


def test_reaper_task_publishes_each_reaped_run_and_closes_session(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class _Session:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class _Settings:
        stuck_run_threshold_minutes = 75

    session = _Session()
    reaped = [SimpleNamespace(id=uuid.uuid4()), SimpleNamespace(id=uuid.uuid4())]
    published: list[uuid.UUID] = []

    monkeypatch.setattr(tasks, "get_session", lambda: session)
    monkeypatch.setattr(tasks, "get_settings", lambda: _Settings())

    def _capture(_session: Any, *, threshold_minutes: int) -> list[Any]:
        captured["session"] = _session
        captured["threshold_minutes"] = threshold_minutes
        return reaped

    monkeypatch.setattr(run_service, "reap_stuck_runs", _capture)
    monkeypatch.setattr(
        alert_dispatch,
        "publish_run_outcome",
        lambda _session, *, run_id: published.append(run_id) or True,
    )

    assert tasks.reap_stuck_runs() == 2
    assert captured["session"] is session
    assert captured["threshold_minutes"] == 75
    assert published == [r.id for r in reaped]  # one alert per reaped run
    assert session.closed is True
