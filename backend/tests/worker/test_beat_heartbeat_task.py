"""Wiring test for the `beat_heartbeat` task (#904, echoed to `workspace_health` for #1885)."""

from typing import Any

from backend.app.services import workspace_health_service
from backend.app.worker import tasks


class _FakeTickStore:
    def __init__(self) -> None:
        self.ticks: list[str] = []

    def set(self, name: str, value: str) -> None:
        self.ticks.append(value)

    def get(self, name: str) -> None:
        return None


def test_beat_heartbeat_writes_both_redis_and_workspace_health(
    monkeypatch: Any, db_session: Any
) -> None:
    store = _FakeTickStore()
    monkeypatch.setattr(tasks, "_heartbeat_store", lambda: store)
    monkeypatch.setattr(tasks, "get_session", lambda: db_session)

    assert tasks.beat_heartbeat() is True
    assert len(store.ticks) == 1
    assert workspace_health_service.read_beat_heartbeat(db_session) is not None


def test_beat_heartbeat_is_idempotent_across_ticks(monkeypatch: Any, db_session: Any) -> None:
    from sqlalchemy import select

    from backend.app.db.models import WorkspaceHealth

    store = _FakeTickStore()
    monkeypatch.setattr(tasks, "_heartbeat_store", lambda: store)
    monkeypatch.setattr(tasks, "get_session", lambda: db_session)

    tasks.beat_heartbeat()
    tasks.beat_heartbeat()

    rows = db_session.scalars(
        select(WorkspaceHealth).where(
            WorkspaceHealth.key == workspace_health_service.BEAT_HEARTBEAT_KEY
        )
    ).all()
    assert len(rows) == 1


def test_a_failed_db_write_does_not_stop_the_redis_tick(monkeypatch: Any) -> None:
    """Either write failing must not skip the other — Redis (the in-process
    watchdog) and `workspace_health` (the admin read API) are independent signals.
    """
    store = _FakeTickStore()
    monkeypatch.setattr(tasks, "_heartbeat_store", lambda: store)

    class _BrokenSession:
        def close(self) -> None:
            pass

    monkeypatch.setattr(tasks, "get_session", lambda: _BrokenSession())
    monkeypatch.setattr(
        workspace_health_service,
        "record_beat_heartbeat",
        lambda _session: (_ for _ in ()).throw(RuntimeError("db down")),
    )

    assert tasks.beat_heartbeat() is False
    assert len(store.ticks) == 1  # the Redis write still happened


def test_a_failed_redis_write_does_not_stop_the_db_write(monkeypatch: Any, db_session: Any) -> None:
    def _broken_store() -> Any:
        raise RuntimeError("redis down")

    monkeypatch.setattr(tasks, "_heartbeat_store", _broken_store)
    monkeypatch.setattr(tasks, "get_session", lambda: db_session)

    assert tasks.beat_heartbeat() is False
    assert workspace_health_service.read_beat_heartbeat(db_session) is not None
