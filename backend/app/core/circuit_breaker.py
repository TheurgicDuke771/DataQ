"""One circuit breaker, shared by every Redis-counter store (#784, #1016, #1135).

The control this implements exists because **bounded socket timeouts do not help
when Redis is up and degraded.** They bound the penalty when it is *down* — the
call fails fast and the caller fails open. During a brownout (a GC pause, a noisy
neighbour, a failing-over primary) every request instead waits out the FULL
timeout, so the app's p99 becomes Redis's p99 while we keep hammering a struggling
server. After `trip_after` consecutive failures the caller stops dialling for
`open_seconds` and takes the fail-open path immediately.

Reopening is a **single probe with no extra state**: once the window has passed the
next call simply goes through. If it fails the breaker re-opens; if it succeeds the
counter resets. Deliberately not per-key, not coordinated across workers — this is
a cheap guard against a brownout, and the guard must never become the cost it
exists to avoid.

**Mechanism shared, state never.** Each store owns its own instance. `core.rate_limit`
(HTTP middleware, async) and `services.otp_service` (per-email OTP counters, sync)
hit the same Redis for different jobs, so folding them onto one breaker would mean
an OTP brownout silently switching off API rate limiting, and vice versa — one
subsystem's degradation disabling an unrelated control is exactly the failure a
breaker is supposed to prevent. There is no module-level state here at all, so that
independence is structural rather than a convention someone has to remember.

The class is deliberately synchronous and I/O-free: every method is pure arithmetic
over a clock, so the async middleware and the sync OTP path can share it verbatim.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from backend.app.core.logging import get_logger

log = get_logger(__name__)

#: Consecutive store failures that trip the breaker, and how long it then stays
#: open. Tuned for a brownout, not an outage: five failures is well past a single
#: unlucky request, and five seconds is short enough that the guarded control
#: resumes promptly once Redis recovers — the deliberate bias of ADR 0035 is
#: availability over enforcement, so an open breaker must never be a long-lived
#: state.
DEFAULT_TRIP_AFTER = 5
DEFAULT_OPEN_SECONDS = 5.0


class CircuitBreaker:
    """Consecutive-failure breaker over one dependency. Not thread-safe by design.

    `name` prefixes the two log events (`<name>_breaker_open` / `_closed`), so a
    log query can tell which subsystem's breaker moved.

    `clock` is injectable so the owning module can keep a `_now()` indirection its
    tests monkeypatch. It must be monotonic-ish over the open window; the default
    is `time.monotonic`, which cannot be dragged backwards by an NTP step (a wall
    clock going backwards would extend an open window arbitrarily).

    Not thread-safe, and that is a considered choice, not an oversight: the worst a
    lost update can do is trip one request early or late, and taking a lock on
    every counted request would reintroduce the shared contention the breaker
    exists to remove.
    """

    __slots__ = ("_clock", "_failures", "_name", "_open_seconds", "_open_until", "_trip_after")

    def __init__(
        self,
        *,
        name: str,
        trip_after: int = DEFAULT_TRIP_AFTER,
        open_seconds: float = DEFAULT_OPEN_SECONDS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._name = name
        self._trip_after = trip_after
        self._open_seconds = open_seconds
        self._clock = clock or time.monotonic
        self._failures = 0
        self._open_until = 0.0

    def is_open(self) -> bool:
        """True while the breaker is holding calls off the dependency entirely."""
        return self._clock() < self._open_until

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._trip_after and not self.is_open():
            self._open_until = self._clock() + self._open_seconds
            log.warning(
                f"{self._name}_breaker_open",
                consecutive_failures=self._failures,
                open_seconds=self._open_seconds,
            )

    def record_success(self) -> None:
        if self.is_open():
            # A success arriving WHILE open is a straggler: a call that passed the
            # gate before the trip and only resolved afterwards. Letting it reset
            # the state would retroactively close a breaker that concurrent
            # failures had just legitimately opened — and a degraded Redis serves
            # exactly this mix of slow successes and timeouts, so the breaker would
            # flap instead of holding. The window closes on time, or on a probe
            # once it has passed.
            return
        if self._failures:
            log.info(f"{self._name}_breaker_closed", after_failures=self._failures)
        self._failures = 0
        self._open_until = 0.0

    def reset(self) -> None:
        """Drop all state — a test hook, and what a store's reset hook calls."""
        self._failures = 0
        self._open_until = 0.0


__all__ = ["DEFAULT_OPEN_SECONDS", "DEFAULT_TRIP_AFTER", "CircuitBreaker"]
