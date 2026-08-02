"""Wiring test for the `purge_otp_codes` beat entry point (#1136).

Pure-unit (no DB): the DELETE behaviour itself is covered DB-backed in
`tests/services/test_otp_service.py`. Here we only assert the task reads the
configured retention window, delegates to the service, returns the deleted
count, and always closes its session — mirroring `test_retention_task.py`'s
`purge_sample_failures` sibling. The beat-schedule REGISTRATION half (the part
that silently rots — #1099) is asserted separately in `test_celery_app.py`.
"""

from typing import Any

from backend.app.services import otp_service
from backend.app.worker import tasks


def test_purge_task_passes_configured_retention_and_closes_session(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class _Session:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class _Settings:
        otp_codes_retention_hours = 48

    session = _Session()
    monkeypatch.setattr(tasks, "get_session", lambda: session)
    monkeypatch.setattr(tasks, "get_settings", lambda: _Settings())

    def _capture(_session: Any, *, older_than_hours: int) -> int:
        captured["session"] = _session
        captured["older_than_hours"] = older_than_hours
        return 9

    monkeypatch.setattr(otp_service, "purge_expired_codes", _capture)

    assert tasks.purge_otp_codes() == 9
    assert captured["session"] is session
    assert captured["older_than_hours"] == 48
    assert session.closed is True


def test_purge_task_closes_session_even_if_the_service_raises(monkeypatch: Any) -> None:
    """The session must never leak on a failure path, even though (unlike its
    orphan-asset/orphan-secret siblings) this task does not fail-soft — a DB error
    here should surface as a failed Celery task, not be swallowed."""

    class _Session:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class _Settings:
        otp_codes_retention_hours = 24

    session = _Session()
    monkeypatch.setattr(tasks, "get_session", lambda: session)
    monkeypatch.setattr(tasks, "get_settings", lambda: _Settings())

    def _boom(_session: Any, *, older_than_hours: int) -> int:
        raise RuntimeError("db is on fire")

    monkeypatch.setattr(otp_service, "purge_expired_codes", _boom)

    try:
        tasks.purge_otp_codes()
        raised = False
    except RuntimeError:
        raised = True

    assert raised, "expected the service's RuntimeError to propagate"
    assert session.closed is True
