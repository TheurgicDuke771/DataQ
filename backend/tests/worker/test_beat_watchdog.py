"""Beat liveness watchdog (#904) — the decision logic, tested without threads.

The bug being defended against has a very specific shape: the worker is UP,
Celery says ``ready``, the schedule is armed, nothing raises — and no scheduled
task executes for hours. It happened three times (2026-07-18, the #905 outage,
2026-07-19) and every time a human had to notice and restart the revision.

So the tests here are about the two ways a watchdog can be wrong, both worse
than the bug:

- **failing to kill** a genuinely wedged worker (the outage continues), and
- **killing** one that is merely starting up, idle-but-fine, or looking at an
  unreadable broker (a restart loop that buries the real cause — #852's lesson).
"""

from __future__ import annotations

from typing import Any

from backend.app.worker import beat_watchdog as wd


class FakeStore:
    """Minimal Redis stand-in; `fail` makes reads raise like a broker outage."""

    def __init__(self, value: str | bytes | None = None, *, fail: bool = False) -> None:
        self.value = value
        self.fail = fail
        self.written: list[tuple[str, str]] = []

    def set(self, name: str, value: str) -> None:
        self.written.append((name, value))
        self.value = value

    def get(self, name: str) -> Any:
        if self.fail:
            raise ConnectionError("broker unreachable")
        return self.value


# ── the pure verdict ────────────────────────────────────────────────────────

_WINDOW = 600.0


def _verdict(**over: Any) -> str:
    kwargs: dict[str, Any] = {
        "last_tick": 1000.0,
        "now_ts": 1100.0,
        "uptime_s": 3600.0,
        "stale_after_s": _WINDOW,
        "grace_s": _WINDOW,
    }
    kwargs.update(over)
    return wd.liveness_verdict(**kwargs)


def test_recent_tick_is_ok() -> None:
    assert _verdict(last_tick=1000.0, now_ts=1100.0) == "ok"


def test_tick_older_than_the_window_is_stale() -> None:
    """The outage signature: up, armed, and nothing executed for 10× the tick."""
    assert _verdict(last_tick=1000.0, now_ts=1000.0 + _WINDOW + 1) == "stale"


def test_exactly_at_the_window_is_still_ok() -> None:
    """Strictly greater-than, so a tick landing on the boundary isn't a kill."""
    assert _verdict(last_tick=1000.0, now_ts=1000.0 + _WINDOW) == "ok"


def test_inside_the_boot_grace_is_unknown_even_when_stale() -> None:
    """A cold start (migrations, slow broker) must never read as a wedge."""
    assert _verdict(last_tick=1.0, now_ts=99999.0, uptime_s=_WINDOW - 1) == "unknown"


def test_no_heartbeat_at_all_is_unknown() -> None:
    """Absent ≠ stale: nothing has proven this worker was ever consuming."""
    assert _verdict(last_tick=None) == "unknown"


# ── the store ───────────────────────────────────────────────────────────────


def test_read_returns_none_and_never_raises_when_the_store_is_down() -> None:
    """An exception on the watchdog thread would silently kill the watchdog —
    the exact class of silent death this module exists to end."""
    assert wd.read_beat_tick(FakeStore(fail=True)) is None


def test_read_handles_bytes_and_malformed_values() -> None:
    assert wd.read_beat_tick(FakeStore(b"1234.5")) == 1234.5
    assert wd.read_beat_tick(FakeStore("not-a-number")) is None


def test_record_writes_the_epoch_stamp() -> None:
    from datetime import UTC, datetime

    store = FakeStore()
    wd.record_beat_tick(store, now=datetime(2026, 7, 19, tzinfo=UTC))
    key, value = store.written[0]
    assert key == wd.BEAT_TICK_KEY
    assert float(value) == datetime(2026, 7, 19, tzinfo=UTC).timestamp()


# ── the loop's kill decision ────────────────────────────────────────────────


def _run_loop(store: FakeStore, *, started_at: float, iterations: int = 1) -> list[Any]:
    kills: list[Any] = []
    wd.watchdog_loop(
        store,
        stale_after_s=_WINDOW,
        grace_s=_WINDOW,
        interval_s=0,
        started_at=started_at,
        terminate=lambda reason, age_s: kills.append((reason, age_s)),
        iterations=iterations,
    )
    return kills


def test_loop_kills_a_worker_whose_heartbeat_went_stale() -> None:
    import time

    # Booted long ago (past grace), last tick far older than the window.
    store = FakeStore(str(time.time() - _WINDOW - 60))
    assert len(_run_loop(store, started_at=time.time() - 10_000)) == 1


def test_loop_does_not_kill_while_the_heartbeat_is_fresh() -> None:
    import time

    store = FakeStore(str(time.time()))
    assert _run_loop(store, started_at=time.time() - 10_000) == []


def test_loop_does_not_kill_when_the_store_is_unreadable() -> None:
    """Restarting cannot fix a broker outage, and a crash loop would bury it."""
    import time

    assert _run_loop(FakeStore(fail=True), started_at=time.time() - 10_000) == []


def test_loop_does_not_kill_a_worker_that_never_ticked() -> None:
    """Third guard: only ever kill a worker that demonstrably WAS executing
    tasks and then stopped — never one that has yet to produce a first tick."""
    import time

    assert _run_loop(FakeStore(None), started_at=time.time() - 10_000) == []
