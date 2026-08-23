"""One circuit breaker, shared by every Redis-counter store (#784, #1016, #1135)."""

from __future__ import annotations

import time
from collections.abc import Callable

from backend.app.core.logging import get_logger

log = get_logger(__name__)

#: Consecutive store failures that trip the breaker, and how long it then stays open.
DEFAULT_TRIP_AFTER = 5
DEFAULT_OPEN_SECONDS = 5.0


class CircuitBreaker:
    """Consecutive-failure breaker over one dependency. Not thread-safe by design."""

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
            # A success arriving WHILE open is a straggler: a call that passed the gate before the
            # trip and only resolved afterwards.
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
