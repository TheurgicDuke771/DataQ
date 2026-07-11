"""Wiring test for the `refresh_lineage_pull` beat entry point (#762).

Pure-unit (no DB / no network): the pull-parse-upsert behaviour is covered DB-backed
in `tests/lineage/test_pull.py`. Here we only assert the beat task is **dark by
default** (no provider → no session opened, returns 0), and otherwise delegates to
`lineage.pull.refresh_pulled_edges` and always closes its session.
"""

from typing import Any

from backend.app.lineage import pull as lineage_pull
from backend.app.worker import tasks


def test_task_no_ops_when_provider_unconfigured(monkeypatch: Any) -> None:
    opened = False

    def _session() -> Any:
        nonlocal opened
        opened = True
        raise AssertionError("session must not open on the dark path")

    monkeypatch.setattr(lineage_pull, "get_lineage_provider", lambda: None)
    monkeypatch.setattr(tasks, "get_session", _session)

    assert tasks.refresh_lineage_pull() == 0
    assert opened is False


def test_task_delegates_and_closes_session(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class _Session:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class _Provider:
        provider = "marquez"

    session = _Session()
    provider = _Provider()
    monkeypatch.setattr(lineage_pull, "get_lineage_provider", lambda: provider)
    monkeypatch.setattr(tasks, "get_session", lambda: session)

    def _refresh(_session: Any, *, provider: Any) -> int:
        captured["session"] = _session
        captured["provider"] = provider
        return 7

    monkeypatch.setattr(lineage_pull, "refresh_pulled_edges", _refresh)

    assert tasks.refresh_lineage_pull() == 7
    assert captured["session"] is session
    assert captured["provider"] is provider
    assert session.closed is True


def test_task_coerces_none_result_to_zero(monkeypatch: Any) -> None:
    class _Session:
        def close(self) -> None:
            pass

    monkeypatch.setattr(lineage_pull, "get_lineage_provider", lambda: object())
    monkeypatch.setattr(tasks, "get_session", lambda: _Session())
    monkeypatch.setattr(lineage_pull, "refresh_pulled_edges", lambda *a, **k: None)

    # refresh returning None (fail-soft skip) surfaces as 0, not None
    assert tasks.refresh_lineage_pull() == 0
