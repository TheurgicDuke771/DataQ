"""Detected seasonal periods (ADR 0048 decision 5): read from a check's history, stored
nowhere, changing no score."""

from __future__ import annotations

import random
import uuid
from datetime import date, timedelta
from typing import Any

from backend.app.datasources import monitors
from backend.app.services import seasonal
from backend.app.services.seasonal import Calendar, DeclaredPeriod

_DAILY: dict[str, Any] = {
    "target_metric": "period_row_count",
    "date_column": "ordered_at",
    "window": 14,
    "min_points": 7,
    "yearly": True,
}
# A November sale that moves by a few days each year, as a Black Friday does.
_SALES = (
    (date(2023, 11, 24), date(2023, 11, 30)),
    (date(2024, 11, 29), date(2024, 12, 5)),
    (date(2025, 11, 28), date(2025, 12, 4)),
)


def _params(**overrides: Any) -> monitors.AnomalyParams:
    return monitors.anomaly_params({**_DAILY, **overrides})


def _noisy(
    start: date,
    end: date,
    *,
    peaks: tuple[tuple[date, date], ...] = (),
    factor: float = 2.5,
    seed: int = 7,
    step: int = 1,
) -> dict[date, float]:
    """About 1000 a period, ±5% noise and a slow upward trend, times ``factor`` in a peak.
    Noise and trend matter: an exactly repeating series hides a level computed at the
    wrong scale."""
    rng = random.Random(seed)
    out: dict[date, float] = {}
    day = start
    while day < end:
        months = (day - start).days / 30
        value = 1000 * (1 + rng.uniform(-0.05, 0.05)) * 1.01**months
        if any(first <= day <= last for first, last in peaks):
            value *= factor
        out[day] = value
        day += timedelta(days=step)
    return out


def test_a_sale_that_moves_a_few_days_each_year_is_one_recurring_period() -> None:
    points = _noisy(date(2023, 1, 2), date(2026, 1, 15), peaks=_SALES)

    found = seasonal.detect(points, _params())

    assert found.reason is None
    assert [p.kind for p in found.periods] == ["recurring"]
    period = found.periods[0]
    assert period.years_seen == 3
    assert [(o.start, o.end) for o in period.occurrences] == list(_SALES)
    assert (period.start, period.end) == _SALES[-1]
    # The span all three cover, laid on one year: what promoting it would declare.
    assert period.range == (11, 24, 12, 5)
    assert 2.2 < period.peak_ratio < 2.9
    assert found.years_of_history > 3


def test_ordinary_noise_and_a_growing_trend_are_not_periods() -> None:
    points = _noisy(date(2023, 1, 2), date(2026, 1, 15), factor=1.0)

    found = seasonal.detect(points, _params())

    assert found.reason is None and found.periods == ()
    assert found.evaluated > 1000


def test_a_peak_seen_in_one_year_only_is_seen_once_with_no_range() -> None:
    spring = (date(2025, 4, 10), date(2025, 4, 13))
    points = _noisy(date(2023, 1, 2), date(2026, 1, 15), peaks=(*_SALES, spring))

    found = seasonal.detect(points, _params())

    once = [p for p in found.periods if p.kind == "seen_once"]
    assert [(p.start, p.end, p.years_seen, p.range) for p in once] == [(*spring, 1, None)]
    assert [p.kind for p in found.periods] == ["seen_once", "recurring"]


def test_one_day_is_a_spike_not_a_period_and_two_days_are_a_period() -> None:
    one = (date(2024, 6, 1), date(2024, 6, 1))
    two = (date(2024, 9, 1), date(2024, 9, 2))
    points = _noisy(date(2024, 1, 1), date(2024, 12, 31), peaks=(one, two), factor=4.0)

    found = seasonal.detect(points, _params())

    assert [(p.start, p.end) for p in found.periods] == [two]


def test_adjacent_qualifying_days_merge_into_one_occurrence_and_a_gap_splits_them() -> None:
    sale = (date(2024, 5, 6), date(2024, 5, 11))
    points = _noisy(date(2024, 1, 1), date(2024, 12, 31), peaks=(sale,))
    split = dict(points)
    del split[date(2024, 5, 8)]  # no point that day: the run cannot bridge it

    merged = seasonal.detect(points, _params())
    broken = seasonal.detect(split, _params())

    assert [(o.start, o.end, o.periods) for p in merged.periods for o in p.occurrences] == [
        (sale[0], sale[1], 6)
    ]
    assert [(o.start, o.end) for p in broken.periods for o in p.occurrences] == [
        (date(2024, 5, 6), date(2024, 5, 7)),
        (date(2024, 5, 9), date(2024, 5, 11)),
    ]


def test_a_sale_split_by_an_ordinary_day_counts_as_one_year_not_two() -> None:
    sales = (
        (date(2024, 11, 25), date(2024, 11, 27)),
        (date(2024, 11, 29), date(2024, 12, 1)),
    )
    points = _noisy(date(2024, 1, 1), date(2025, 1, 31), peaks=sales)

    found = seasonal.detect(points, _params())

    assert [(p.kind, p.years_seen, len(p.occurrences)) for p in found.periods] == [
        ("seen_once", 1, 2)
    ]


def test_a_period_across_the_year_end_recurs_with_a_wrapping_range() -> None:
    sales = (
        (date(2023, 12, 28), date(2024, 1, 3)),
        (date(2024, 12, 27), date(2025, 1, 2)),
    )
    points = _noisy(date(2023, 1, 2), date(2025, 3, 1), peaks=sales)

    found = seasonal.detect(points, _params())

    assert [(p.kind, p.years_seen, p.range) for p in found.periods] == [
        ("recurring", 2, (12, 27, 1, 3))
    ]


def test_weekly_occurrences_end_on_sunday_and_one_week_is_enough() -> None:
    start = date(2023, 1, 2)  # a Monday
    peaks = ((date(2023, 11, 20), date(2023, 11, 20)), (date(2024, 11, 25), date(2024, 11, 25)))
    points = _noisy(start, date(2025, 1, 6), peaks=peaks, step=7)

    found = seasonal.detect(points, _params(period="week", window=8, min_points=4))

    assert [(p.kind, p.start, p.end) for p in found.periods] == [
        ("recurring", date(2024, 11, 25), date(2024, 12, 1))
    ]


def test_backfilled_points_are_counted_per_occurrence() -> None:
    points = _noisy(date(2023, 1, 2), date(2026, 1, 15), peaks=_SALES)
    seeded = frozenset(d for d in points if d < date(2025, 1, 1))

    found = seasonal.detect(points, _params(), backfilled=seeded)

    assert [o.backfilled for o in found.periods[0].occurrences] == [7, 7, 0]
    assert found.backfilled == len(seeded)


def test_too_little_history_is_empty_with_a_reason() -> None:
    few = _noisy(date(2025, 1, 1), date(2025, 1, 6))

    assert seasonal.detect(few, _params()).reason == seasonal.INSUFFICIENT_HISTORY
    assert seasonal.detect({}, _params()).reason == seasonal.NO_HISTORY
    whole_table = monitors.anomaly_params({"target_metric": "row_count"})
    assert seasonal.detect(few, whole_table).reason == seasonal.NOT_PER_PERIOD


def test_the_level_is_the_scoring_level_so_an_attached_period_is_detected_whole() -> None:
    """A sale longer than half the window lifts the median it is compared with, so its tail
    is not seen; an attached declared period is left out of the level, as in scoring."""
    sale = (date(2024, 11, 4), date(2024, 11, 24))
    points = _noisy(date(2024, 1, 1), date(2024, 12, 31), peaks=(sale,))
    entry = DeclaredPeriod(
        id=uuid.uuid4(),
        name="November",
        kind="dated",
        start_date=sale[0],
        end_date=sale[1],
    )

    bare = seasonal.detect(points, _params())
    declared = seasonal.detect(points, _params(), Calendar(entries=(entry,)))

    bare_end = bare.periods[0].end
    assert sale[0] == bare.periods[0].start and bare_end < sale[1]
    assert [(p.start, p.end) for p in declared.periods] == [sale]
    assert [e.name for e in declared.periods[0].declared] == ["November"]
