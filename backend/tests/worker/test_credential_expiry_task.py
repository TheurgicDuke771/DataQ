"""Wiring test for the `refresh_credential_expiry` beat entry point (#838).

Pure-unit (no DB): the sweep's own behaviour is covered DB-backed in
`tests/services/test_credential_expiry.py`. Here we only assert the task
delegates, returns the changed count, always closes its session, and is
fail-soft — the sweep exists to *produce a warning*, so a Key Vault outage
inside it must not be promoted into a failed Celery task, and must not take
down the beat tick for the janitors scheduled after it.
"""

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
    monkeypatch.setattr(tasks, "get_secret_store", lambda: object())

    def _boom(_session: Any, *, secret_store: Any) -> int:
        raise RuntimeError("Key Vault unreachable")

    monkeypatch.setattr(connection_service, "refresh_credential_expiry", _boom)

    assert tasks.refresh_credential_expiry() == 0
    assert session.rolled_back is True
    assert session.closed is True


def test_the_sweep_is_actually_scheduled(monkeypatch: Any) -> None:
    """A task nobody runs warns nobody.

    The whole feature is a *periodic* re-read; an entry point that exists but is
    absent from the beat schedule would leave every credential stored before this
    shipped permanently unknown, while every unit test above still passed.
    """
    from backend.app.worker.celery_app import celery_app

    scheduled = {entry["task"] for entry in celery_app.conf.beat_schedule.values()}
    assert "refresh_credential_expiry" in scheduled
