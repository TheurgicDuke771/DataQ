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
    """The outage signature: up, armed, and nothing executed for ten times the tick."""
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


def _run_loop(
    store: FakeStore,
    *,
    started_at: float,
    iterations: int = 3,
    active: int = 0,
) -> list[Any]:
    """Run the loop with a stubbed terminate/active-tasks seam and collect kills."""
    kills: list[Any] = []
    wd.watchdog_loop(
        store,
        stale_after_s=_WINDOW,
        grace_s=_WINDOW,
        interval_s=0,
        started_at=started_at,
        terminate=lambda reason, age_s: kills.append((reason, age_s)),
        active_tasks=lambda: active,
        iterations=iterations,
    )
    return kills


def test_loop_kills_after_the_heartbeat_this_worker_produced_goes_stale() -> None:
    """The #905 shape end to end: this incarnation ticks, then stops."""
    import time

    booted = time.time() - 10_000
    # A tick produced AFTER boot (so guard 3 arms) but long ago (so it's stale).
    store = FakeStore(str(booted + 1))
    kills = _run_loop(store, started_at=booted)
    assert len(kills) == 1


def test_loop_needs_consecutive_confirmations_before_killing() -> None:
    """Guard 5: one stale reading can be a clock step, so a single pass must not
    kill — the streak has to survive `STALE_CONFIRMATIONS` iterations."""
    import time

    booted = time.time() - 10_000
    store = FakeStore(str(booted + 1))
    assert _run_loop(store, started_at=booted, iterations=wd.STALE_CONFIRMATIONS - 1) == []


def test_loop_does_not_kill_while_the_heartbeat_is_fresh() -> None:
    import time

    store = FakeStore(str(time.time()))
    assert _run_loop(store, started_at=time.time() - 10_000) == []


def test_loop_does_not_kill_when_the_store_is_unreadable() -> None:
    """Guard 2: restarting cannot fix a broker outage, and a crash loop would
    bury it (#852)."""
    import time

    assert _run_loop(FakeStore(fail=True), started_at=time.time() - 10_000) == []


def test_loop_does_not_kill_a_worker_that_never_ticked() -> None:
    import time

    assert _run_loop(FakeStore(None), started_at=time.time() - 10_000) == []


def test_loop_ignores_a_stale_key_left_by_a_PREVIOUS_worker() -> None:
    """Guard 3, the crash-loop bug found in review: the stamp never expires, so
    a predecessor's key is readable the instant a new worker boots. If that
    armed the watchdog, a worker that never consumes anything would kill itself
    on someone else's tick, restart, and loop forever on an ever-staler key."""
    import time

    booted = time.time()
    predecessor_tick = booted - 10_000  # older than boot AND older than the window
    assert _run_loop(FakeStore(str(predecessor_tick)), started_at=booted - _WINDOW - 1) == []


def test_loop_does_not_kill_while_tasks_are_running() -> None:
    """Guard 4: a stale beat with a busy pool is a long GX run holding the
    slots, not a wedge — hard-exiting would abort real work mid-flight and
    strand its `runs` row."""
    import time

    booted = time.time() - 10_000
    store = FakeStore(str(booted + 1))
    assert _run_loop(store, started_at=booted, active=1) == []


def test_loop_recovers_the_streak_when_work_resumes() -> None:
    """A blip must not accumulate toward a kill: a stale reading followed by a
    fresh one resets the streak, so the next stale reading starts from zero."""
    import time

    booted = time.time() - 10_000

    class FlappingStore(FakeStore):
        def __init__(self) -> None:
            super().__init__()
            self.reads = 0

        def get(self, name: str) -> Any:
            self.reads += 1
            # stale, then fresh, then stale again — never two stale in a row.
            return str(booted + 1) if self.reads % 2 else str(time.time())

    assert _run_loop(FlappingStore(), started_at=booted, iterations=6) == []


def test_negative_age_is_never_acted_on() -> None:
    """A backwards clock step yields a future-dated tick; that reading is
    meaningless and must not count toward a kill."""
    import time

    booted = time.time() - 10_000
    assert _run_loop(FakeStore(str(time.time() + 5_000)), started_at=booted) == []


def test_active_task_count_is_zero_outside_a_worker() -> None:
    """'Unknown' must resolve to 0, never to 'busy' — a permanently-busy reading
    would disarm the watchdog entirely."""
    assert wd.active_task_count() == 0


def test_build_store_bounds_its_socket_timeouts() -> None:
    """The watchdog's own Redis client must never block forever (#854): an
    untimed read hangs the one thread whose job is to notice hangs."""
    client = wd.build_store("redis://localhost:6379/0")
    kwargs = client.connection_pool.connection_kwargs  # type: ignore[attr-defined]
    assert kwargs["socket_timeout"] == wd.REDIS_READ_TIMEOUT_S
    assert kwargs["socket_connect_timeout"] == wd.REDIS_CONNECT_TIMEOUT_S
