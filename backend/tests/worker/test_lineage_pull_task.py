"""Wiring test for the `refresh_lineage_pull` beat entry point (#762, #1090).

Pure-unit (no DB / no network): the pull-parse-upsert behaviour is covered DB-backed
in `tests/lineage/test_pull.py`. Here we assert the task's three provider states wire
correctly: **unset** → purge orphaned pulled edges (#1090) and return 0;
**configured-but-broken** (typo'd name / missing URL) → true no-op, no session, cache
kept; **configured** → delegate to `lineage.pull.refresh_pulled_edges`. The session
always closes.
"""

from typing import Any

from backend.app.lineage import pull as lineage_pull
from backend.app.worker import tasks


def test_task_purges_orphans_when_provider_is_unset(monkeypatch: Any) -> None:
    """#1090: LINEAGE_PROVIDER removed entirely → cached pulled edges are orphans
    that would render as current forever; the daily tick sweeps them."""

    class _Session:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    session = _Session()
    purged_with: list[Any] = []
    monkeypatch.setattr(lineage_pull, "get_lineage_provider", lambda: None)
    monkeypatch.setattr(lineage_pull, "lineage_provider_unset", lambda: True)
    monkeypatch.setattr(lineage_pull, "purge_orphaned_pulled_edges", purged_with.append)
    monkeypatch.setattr(tasks, "get_session", lambda: session)

    assert tasks.refresh_lineage_pull() == 0
    assert purged_with == [session]
    assert session.closed is True


def test_task_keeps_the_cache_when_provider_is_configured_but_broken(monkeypatch: Any) -> None:
    """A typo'd provider name / missing URL also yields provider=None — but purging
    there would turn a one-character misconfiguration into data loss. True no-op:
    no session, cache untouched."""
    opened = False

    def _session() -> Any:
        nonlocal opened
        opened = True
        raise AssertionError("session must not open on the misconfigured path")

    monkeypatch.setattr(lineage_pull, "get_lineage_provider", lambda: None)
    monkeypatch.setattr(lineage_pull, "lineage_provider_unset", lambda: False)
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

    monkeypatch.setattr(lineage_pull, "get_lineage_provider", object)
    monkeypatch.setattr(tasks, "get_session", lambda: _Session())
    monkeypatch.setattr(lineage_pull, "refresh_pulled_edges", lambda *a, **k: None)

    # refresh returning None (fail-soft skip) surfaces as 0, not None
    assert tasks.refresh_lineage_pull() == 0
