"""Wiring test for the `sweep_orphan_assets` beat entry point (#770).

Pure-unit (no DB): the reference-guarded delete behaviour is covered DB-backed
in `tests/services/test_asset_sweep.py`. Here we only assert the task reads the
configured retention window, delegates to the service, returns the swept
count, always closes its session, and — unlike its sibling janitors — is
fail-soft on a DB error: the hand-maintained reference-guard checklist
(ADR 0034; see the service docstring) is new and a future referencing table
landing without its guard line must not crash the beat tick for the janitors
scheduled after it.
"""

from typing import Any

from backend.app.services import asset_service
from backend.app.worker import tasks


def test_sweep_task_passes_configured_retention_and_closes_session(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class _Session:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class _Settings:
        asset_orphan_retention_days = 45

    session = _Session()
    monkeypatch.setattr(tasks, "get_session", lambda: session)
    monkeypatch.setattr(tasks, "get_settings", lambda: _Settings())

    def _capture(_session: Any, *, retention_days: int) -> int:
        captured["session"] = _session
        captured["retention_days"] = retention_days
        return 3

    monkeypatch.setattr(asset_service, "sweep_orphan_assets", _capture)

    assert tasks.sweep_orphan_assets() == 3
    assert captured["session"] is session
    assert captured["retention_days"] == 45
    assert session.closed is True


def test_sweep_task_fails_soft_on_db_error(monkeypatch: Any) -> None:
    """A DB hiccup inside the service call must not propagate — the task returns
    0, rolls back, and still closes the session (never crashes the beat tick)."""

    class _Session:
        def __init__(self) -> None:
            self.closed = False
            self.rolled_back = False

        def rollback(self) -> None:
            self.rolled_back = True

        def close(self) -> None:
            self.closed = True

    class _Settings:
        asset_orphan_retention_days = 30

    session = _Session()
    monkeypatch.setattr(tasks, "get_session", lambda: session)
    monkeypatch.setattr(tasks, "get_settings", lambda: _Settings())

    def _boom(_session: Any, *, retention_days: int) -> int:
        raise RuntimeError("db is on fire")

    monkeypatch.setattr(asset_service, "sweep_orphan_assets", _boom)

    assert tasks.sweep_orphan_assets() == 0
    assert session.rolled_back is True
    assert session.closed is True


def test_sweep_task_fails_soft_when_settings_lookup_raises(monkeypatch: Any) -> None:
    """Even a failure before the service call (e.g. Settings misconfigured) is
    swallowed — the session still closes."""

    class _Session:
        def __init__(self) -> None:
            self.closed = False
            self.rolled_back = False

        def rollback(self) -> None:
            self.rolled_back = True

        def close(self) -> None:
            self.closed = True

    session = _Session()
    monkeypatch.setattr(tasks, "get_session", lambda: session)

    def _boom_settings() -> Any:
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(tasks, "get_settings", _boom_settings)

    assert tasks.sweep_orphan_assets() == 0
    assert session.rolled_back is True
    assert session.closed is True
