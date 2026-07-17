"""Wiring test for the `refresh_warehouse_lineage` beat entry point (#858, slice 3).

Pure-unit (no DB / no network): the per-connection refresh + persistence is covered
DB-backed in `tests/lineage/test_warehouse_connection_refresh.py`. Here we assert the
beat task is **dark by default** (flag off → no session, returns 0), iterates only the
warehouse connection types, is fail-soft per connection (one failing connection doesn't
abort the sweep), and always closes its session.
"""

from typing import Any

from backend.app.lineage import warehouse_refresh
from backend.app.worker import tasks


def _settings(*, enabled: bool) -> Any:
    class _S:
        warehouse_lineage_enabled = enabled

    return _S()


def test_task_dark_by_default(monkeypatch: Any) -> None:
    opened = False

    def _session() -> Any:
        nonlocal opened
        opened = True
        raise AssertionError("session must not open on the dark path")

    monkeypatch.setattr(tasks, "get_settings", lambda: _settings(enabled=False))
    monkeypatch.setattr(tasks, "get_session", _session)

    assert tasks.refresh_warehouse_lineage() == 0
    assert opened is False


class _FakeConn:
    def __init__(self, cid: str) -> None:
        self.id = cid


class _Session:
    def __init__(self, connections: list[Any]) -> None:
        self._connections = connections
        self.closed = False

    def scalars(self, _stmt: Any) -> Any:
        return iter(self._connections)

    def close(self) -> None:
        self.closed = True


def test_task_refreshes_each_connection_and_is_fail_soft(monkeypatch: Any) -> None:
    conns = [_FakeConn("a"), _FakeConn("b"), _FakeConn("c")]
    session = _Session(conns)
    monkeypatch.setattr(tasks, "get_settings", lambda: _settings(enabled=True))
    monkeypatch.setattr(tasks, "get_session", lambda: session)
    monkeypatch.setattr(tasks, "get_secret_store", lambda: object())

    seen: list[str] = []

    def _refresh(_session: Any, *, connection: Any, secret_store: Any) -> Any:
        seen.append(connection.id)
        if connection.id == "b":
            return None  # one connection's warehouse is unavailable (fail-soft)
        return object()  # a truthy outcome

    monkeypatch.setattr(warehouse_refresh, "refresh_connection_lineage", _refresh)

    # every connection is attempted; the failed one doesn't abort the sweep, and the
    # return counts only the successful refreshes (a + c).
    assert tasks.refresh_warehouse_lineage() == 2
    assert seen == ["a", "b", "c"]
    assert session.closed is True
