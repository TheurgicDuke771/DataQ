"""Unit tests for the cron schedule helpers (A7) — no DB, no Celery.

Covers the two things the scheduling backend leans on: input validation (a bad
cron / timezone is a clean 422, not a worker crash at fire time) and the
no-backfill `next_fire` contract (always the next fire *strictly after* the base,
so a downtime gap collapses to a single fire).
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from backend.app.services import cron

# ───────────────────────── validation ──────────────────────────────


@pytest.mark.parametrize("expr", ["* * * *", "* * * * * *", "@hourly", "0 0 * *", ""])
def test_validate_cron_rejects_non_5_field(expr: str) -> None:
    with pytest.raises(cron.InvalidCronError):
        cron.validate_cron(expr)


def test_validate_cron_rejects_garbage_fields() -> None:
    with pytest.raises(cron.InvalidCronError):
        cron.validate_cron("99 * * * *")


@pytest.mark.parametrize("expr", ["0 0 30 2 *", "0 0 31 4 *", "0 0 31 2 *"])
def test_validate_cron_rejects_impossible_calendar_date(expr: str) -> None:
    """`croniter.is_valid` passes syntactically-valid but unsatisfiable dates
    (Feb 30, Apr 31); these must be a clean 422, not a CroniterBadDateError that
    500s on create or crashes the dispatcher tick."""
    with pytest.raises(cron.InvalidCronError):
        cron.validate_cron(expr)


def test_next_fire_rejects_impossible_calendar_date() -> None:
    with pytest.raises(cron.InvalidCronError):
        cron.next_fire("0 0 30 2 *", "UTC")


def test_validate_cron_accepts_standard_expression() -> None:
    cron.validate_cron("0 6 * * 1-5")  # 06:00 on weekdays — must not raise


def test_validate_timezone_rejects_unknown() -> None:
    with pytest.raises(cron.InvalidTimezoneError):
        cron.validate_timezone("Mars/Phobos")


def test_validate_timezone_returns_zoneinfo() -> None:
    assert cron.validate_timezone("America/New_York") == ZoneInfo("America/New_York")


# ───────────────────────── next_fire ───────────────────────────────


def test_next_fire_is_utc_aware() -> None:
    nxt = cron.next_fire("0 0 * * *", "UTC", after=datetime(2026, 6, 15, 12, 0, tzinfo=UTC))
    assert nxt == datetime(2026, 6, 16, 0, 0, tzinfo=UTC)
    assert nxt.tzinfo is UTC


def test_next_fire_is_strictly_after_base_no_backfill() -> None:
    """On the fire boundary, the next fire is the *following* slot, never the same
    instant — so advancing a just-fired schedule can't re-select it this tick."""
    on_boundary = datetime(2026, 6, 15, 0, 0, tzinfo=UTC)
    assert cron.next_fire("0 0 * * *", "UTC", after=on_boundary) == datetime(
        2026, 6, 16, 0, 0, tzinfo=UTC
    )


def test_next_fire_collapses_downtime_gap_to_one() -> None:
    """An hourly schedule last due 5 hours ago yields the next *future* hour, not
    five backfilled fires (the dispatcher fires once per returned instant)."""
    way_behind = datetime(2026, 6, 15, 5, 30, tzinfo=UTC)
    assert cron.next_fire("0 * * * *", "UTC", after=way_behind) == datetime(
        2026, 6, 15, 6, 0, tzinfo=UTC
    )


def test_next_fire_evaluates_cron_in_named_timezone() -> None:
    """'0 6 * * *' in America/New_York is 06:00 *local*; in mid-June (EDT, UTC-4)
    that normalises to 10:00 UTC — proving the cron is tz-evaluated, not UTC."""
    after = datetime(2026, 6, 15, 0, 0, tzinfo=UTC)
    assert cron.next_fire("0 6 * * *", "America/New_York", after=after) == datetime(
        2026, 6, 15, 10, 0, tzinfo=UTC
    )


def test_next_fire_spring_forward_gap_rolls_past_missing_hour() -> None:
    """'30 2 * * *' on a spring-forward day where 02:30 local doesn't exist must
    still produce a single fire (rolled to the post-gap instant), never raise."""
    # 2026-03-08: US clocks jump 02:00 EST → 03:00 EDT; 02:30 is in the gap.
    after = datetime(2026, 3, 8, 0, 0, tzinfo=UTC)
    fired = cron.next_fire("30 2 * * *", "America/New_York", after=after)
    assert fired == datetime(2026, 3, 8, 7, 0, tzinfo=UTC)  # 03:00 EDT, just past the gap


def test_next_fire_fall_back_overlap_fires_once() -> None:
    """'30 1 * * *' on a fall-back day where 01:30 local occurs twice must fire on
    the first (EDT) occurrence only — the advance then jumps to the next day, so
    the schedule can't double-fire across the repeated hour."""
    # 2026-11-01: US clocks fall 02:00 EDT → 01:00 EST; 01:30 happens twice.
    after = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    fired = cron.next_fire("30 1 * * *", "America/New_York", after=after)
    assert fired == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)  # 01:30 EDT (first), not 06:30


def test_next_fire_validates_before_computing() -> None:
    with pytest.raises(cron.InvalidCronError):
        cron.next_fire("nope", "UTC")
    with pytest.raises(cron.InvalidTimezoneError):
        cron.next_fire("0 0 * * *", "Nowhere/Land")
