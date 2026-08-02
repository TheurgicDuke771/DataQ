"""The shared consecutive-failure breaker (#1135).

`core.rate_limit` and `services.otp_service` both guard a Redis counter with this
class. Its *behaviour* is asserted through each store in their own suites (that is
where "we stopped dialling" is observable); what this file pins is the mechanism
itself — the state machine, and the property that two instances cannot influence
each other, which is the whole reason the extraction was allowed to happen.
"""

from __future__ import annotations

import io
import logging

import pytest

from backend.app.core.circuit_breaker import (
    DEFAULT_OPEN_SECONDS,
    DEFAULT_TRIP_AFTER,
    CircuitBreaker,
)
from backend.app.core.logging import configure_logging


class _Clock:
    """A hand-cranked clock — the open window is a duration, and a test that had to
    sleep through it would be slow AND flaky."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _breaker(clock: _Clock, **kw: object) -> CircuitBreaker:
    return CircuitBreaker(name="test_store", clock=clock, **kw)  # type: ignore[arg-type]


def test_it_trips_only_after_the_configured_run_of_consecutive_failures() -> None:
    """One unlucky call is not a brownout. Tripping on the first blip would hand the
    guarded control to a single dropped packet."""
    clock = _Clock()
    breaker = _breaker(clock, trip_after=3)

    breaker.record_failure()
    breaker.record_failure()
    assert not breaker.is_open()

    breaker.record_failure()
    assert breaker.is_open()


def test_a_success_resets_the_run_so_scattered_blips_never_trip_it() -> None:
    """The counter is CONSECUTIVE failures — a dependency that fails one call in
    three is annoying, not degraded."""
    clock = _Clock()
    breaker = _breaker(clock, trip_after=3)

    for _ in range(10):
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_success()

    assert not breaker.is_open()


def test_the_window_closes_on_its_own_and_a_probe_success_resets_the_count() -> None:
    """Open must be a short, self-clearing state: a breaker that stays open is the
    guarded control silently switched off."""
    clock = _Clock()
    breaker = _breaker(clock, trip_after=2, open_seconds=5.0)

    breaker.record_failure()
    breaker.record_failure()
    assert breaker.is_open()

    clock.t += 5.0  # the window elapses — the next call is the probe
    assert not breaker.is_open()

    breaker.record_success()
    breaker.record_failure()
    assert not breaker.is_open(), "the pre-trip failures survived a successful probe"


def test_a_failed_probe_re_opens_the_window_rather_than_hammering() -> None:
    clock = _Clock()
    breaker = _breaker(clock, trip_after=2, open_seconds=5.0)
    breaker.record_failure()
    breaker.record_failure()

    clock.t += 6.0
    breaker.record_failure()  # the probe fails

    assert breaker.is_open()
    assert breaker._open_until == pytest.approx(clock.t + 5.0)


def test_a_straggling_success_cannot_close_an_open_breaker() -> None:
    """A degraded dependency serves a MIX of slow successes and failures, so a call
    that passed the gate before the trip can resolve just after it. If that straggler
    reset the state it would retroactively close a breaker other callers had
    legitimately opened, and the breaker would flap under exactly the traffic it
    exists to handle."""
    clock = _Clock()
    breaker = _breaker(clock, trip_after=2, open_seconds=5.0)
    breaker.record_failure()
    breaker.record_failure()

    breaker.record_success()

    assert breaker.is_open()


def test_two_breakers_share_no_state() -> None:
    """The one property that makes ONE implementation safe for two subsystems.

    `core.rate_limit` and the OTP counter store hit the same Redis for different
    jobs. If they shared breaker state, an OTP brownout would switch off API rate
    limiting — one subsystem's degradation disabling an unrelated control, which is
    precisely what a breaker is supposed to prevent.
    """
    clock = _Clock()
    a, b = _breaker(clock, trip_after=2), _breaker(clock, trip_after=2)

    a.record_failure()
    a.record_failure()

    assert a.is_open()
    assert not b.is_open()


def test_reset_clears_both_halves_of_the_state() -> None:
    clock = _Clock()
    breaker = _breaker(clock, trip_after=2, open_seconds=5.0)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.is_open()

    breaker.reset()

    assert not breaker.is_open()
    breaker.record_failure()
    assert not breaker.is_open(), "the pre-reset failure count survived"


def test_the_log_events_are_named_after_the_owning_store() -> None:
    """Two breakers now emit these events, so an operator has to be able to tell
    which one moved — and ADR 0035 documents `rate_limit_store_breaker_open` by
    name, so the prefix is a contract, not a cosmetic."""
    configure_logging()
    buffer = io.StringIO()
    handler = logging.getLogger().handlers[0]
    original = handler.stream  # type: ignore[attr-defined]
    handler.stream = buffer  # type: ignore[attr-defined]
    try:
        clock = _Clock()
        breaker = _breaker(clock, trip_after=1, open_seconds=5.0)
        breaker.record_failure()
        clock.t += 6.0
        breaker.record_success()
    finally:
        handler.stream = original  # type: ignore[attr-defined]

    emitted = buffer.getvalue()
    assert "test_store_breaker_open" in emitted
    assert "test_store_breaker_closed" in emitted


def test_the_shared_defaults_are_the_tuning_both_stores_inherit() -> None:
    """Pinned so a change to the shared tuning is a deliberate edit here, not a
    silent retune of two subsystems at once."""
    assert DEFAULT_TRIP_AFTER == 5
    assert DEFAULT_OPEN_SECONDS == 5.0
