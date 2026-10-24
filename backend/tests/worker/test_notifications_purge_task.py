"""Wiring test for the `purge_notifications` beat entry point (#1687)."""

from typing import Any

from backend.app.services import notification_feed_service
from backend.app.worker import tasks


def test_purge_task_passes_configured_retention_and_closes_session(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class _Session:
        closed = False

        def close(self) -> None:
            self.closed = True

    class _Settings:
        notifications_retention_days = 45

    session = _Session()
    monkeypatch.setattr(tasks, "get_session", lambda: session)
    monkeypatch.setattr(tasks, "get_settings", lambda: _Settings())

    def _capture(_session: Any, *, older_than_days: int) -> int:
        captured["session"] = _session
        captured["older_than_days"] = older_than_days
        return 7

    monkeypatch.setattr(notification_feed_service, "purge_expired", _capture)

    assert tasks.purge_notifications() == 7
    assert captured == {"session": session, "older_than_days": 45}
    assert session.closed is True
