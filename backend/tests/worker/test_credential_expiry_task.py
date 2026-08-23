"""Wiring test for the `refresh_credential_expiry` beat entry point (#838)."""

from typing import Any

from backend.app.services import connection_service
from backend.app.worker import tasks


class _Session:
    def __init__(self) -> None:
        self.closed = False
        self.rolled_back = False

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def test_task_delegates_to_the_sweep_and_closes_its_session(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    session = _Session()
    store = object()
    monkeypatch.setattr(tasks, "get_session", lambda: session)
    monkeypatch.setattr(tasks, "get_secret_store", lambda: store)

    def _capture(_session: Any, *, secret_store: Any) -> int:
        captured["session"] = _session
        captured["secret_store"] = secret_store
        return 2

    monkeypatch.setattr(connection_service, "refresh_credential_expiry", _capture)

    assert tasks.refresh_credential_expiry() == 2
    assert captured["session"] is session
    assert captured["secret_store"] is store
    assert session.closed is True


def test_task_fails_soft_when_the_secret_store_is_unreachable(monkeypatch: Any) -> None:
    session = _Session()
    monkeypatch.setattr(tasks, "get_session", lambda: session)
    monkeypatch.setattr(tasks, "get_secret_store", object)

    def _boom(_session: Any, *, secret_store: Any) -> int:
        raise RuntimeError("Key Vault unreachable")

    monkeypatch.setattr(connection_service, "refresh_credential_expiry", _boom)

    assert tasks.refresh_credential_expiry() == 0
    assert session.rolled_back is True
    assert session.closed is True


def test_the_sweep_is_actually_scheduled(monkeypatch: Any) -> None:
    """A task nobody runs warns nobody."""
    from backend.app.worker.celery_app import celery_app

    scheduled = {entry["task"] for entry in celery_app.conf.beat_schedule.values()}
    assert "refresh_credential_expiry" in scheduled
