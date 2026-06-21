"""Wiring test for the `purge_sample_failures` beat entry point.

Pure-unit (no DB): the bulk-UPDATE behaviour is covered DB-backed in
`tests/services/test_run_retention.py`. Here we only assert the task reads the
configured retention window, delegates to the service, and always closes its
session.
"""

from typing import Any

from backend.app.worker import tasks


def test_purge_task_passes_configured_retention_and_closes_session(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class _Session:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class _Settings:
        sample_failures_retention_days = 45

    session = _Session()
    monkeypatch.setattr(tasks, "get_session", lambda: session)
    monkeypatch.setattr(tasks, "get_settings", lambda: _Settings())

    def _capture(_session: Any, *, retention_days: int) -> int:
        captured["session"] = _session
        captured["retention_days"] = retention_days
        return 7

    monkeypatch.setattr(tasks.run_service, "purge_expired_sample_failures", _capture)

    assert tasks.purge_sample_failures() == 7
    assert captured["session"] is session
    assert captured["retention_days"] == 45
    assert session.closed is True
