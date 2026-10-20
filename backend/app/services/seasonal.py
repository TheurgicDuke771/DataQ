"""Yearly seasonality for the anomaly monitor (ADR 0048): a seasonal index over the rolling
baseline, and the declared seasonal calendar it lines up with.

Pure functions over a check's stored points. The *level* at a period is the median of the
points it would ordinarily be scored against; the *index* is what the matching period in an
earlier year was, divided by the level then; the *expected* value is the level now times the
index. A run is scored on how far it is from that **in proportion to it**, against how far
its prior points were from what was expected of them: a sale day five times an ordinary day
has five times the room. Where the expected value is zero (the shop was closed that day
last year) there is no proportion to take, and the distance is taken as it stands.

A declared period (decision 5) changes two things: a day inside one is matched to the same
offset within the period's previous occurrence instead of to the nearest weekday, and days
inside any declared period are left out of the level. With no earlier occurrence in the
history, a value above the level is reported as within the period and not banded; a value
below it is judged as usual.
"""

from __future__ import annotations

import bisect
import calendar as _cal
import math
import statistics
import uuid
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from backend.app.datasources.monitors import (
    ANOMALY_DEGENERATE_Z,
    PERIOD_HISTORY_YEARS,
    AnomalyParams,
)

#: Points before a period needed for its level to mean anything.
_LEVEL_MIN_POINTS = 2
#: A spread this small against the expected value is float rounding, not variation.
_NEGLIGIBLE = 1e-9

# Why the yearly comparison was not applied to a run (``yearly.reason``).
NO_EARLIER_YEAR = "no_earlier_year"
ZERO_LEVEL = "zero_level_in_earlier_year"
NEGATIVE_VALUE = "negative_value"
INSUFFICIENT_HISTORY = "insufficient_history"
#: Every earlier year's matching day fell inside a declared period, so none is an ordinary
#: comparison for an ordinary day. (If any year's matching day was ordinary and merely has
#: no point, the blocker is missing history and the reason is ``NO_EARLIER_YEAR``.)
EARLIER_YEAR_IN_DECLARED_PERIOD = "earlier_year_in_declared_period"

RECURRING = "recurring"
DATED = "dated"


def matched_day(day: date, years_back: int, *, weekly: bool) -> date:
    """The period ``years_back`` years before ``day`` that it is compared with: for a day, the
    same weekday nearest the same calendar date; for a week, the week containing that date.
    (Not the same ISO week number: some years have a week 53 and the earlier year does not.)
    """
    try:
        target = day.replace(year=day.year - years_back)
    except ValueError:  # 29 February
        target = day.replace(year=day.year - years_back, day=28)
    if weekly:
        return target - timedelta(days=target.weekday())
    shift = (day.weekday() - target.weekday()) % 7
    return target + timedelta(days=shift - 7 if shift > 3 else shift)


def month_day(year: int, month: int, day: int) -> date:
    """The date, with 29 February taken as 28 February in a year that has none."""
    return date(year, month, min(day, _cal.monthrange(year, month)[1]))


@dataclass(frozen=True)
class Occurrence:
    """One run of a declared period: ``[start, end]``, both inclusive."""

    start: date
    end: date

    def __contains__(self, day: object) -> bool:
        return isinstance(day, date) and self.start <= day <= self.end


@dataclass(frozen=True)
class DeclaredPeriod:
    """A calendar entry as the scorer sees it (``seasonal_periods`` row)."""

    id: uuid.UUID
    name: str
    kind: str
    start_month: int | None = None
    start_day: int | None = None
    end_month: int | None = None
    end_day: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    series: str | None = None

    @property
    def wraps(self) -> bool:
        """A recurring range that crosses the year end (20 December to 5 January)."""
        assert self.start_month and self.start_day and self.end_month and self.end_day
        return (self.end_month, self.end_day) < (self.start_month, self.start_day)

    def occurrence(self, start_year: int) -> Occurrence:
        """The occurrence of a recurring entry that starts in ``start_year``."""
        assert self.start_month and self.start_day and self.end_month and self.end_day
        end_year = start_year + 1 if self.wraps else start_year
        return Occurrence(
            month_day(start_year, self.start_month, self.start_day),
            month_day(end_year, self.end_month, self.end_day),
        )

    def containing(self, day: date) -> Occurrence | None:
        """The occurrence ``day`` falls in, if any."""
        if self.kind == DATED:
            assert self.start_date is not None and self.end_date is not None
            dated = Occurrence(self.start_date, self.end_date)
            return dated if day in dated else None
        for start_year in (day.year, day.year - 1):
            occurrence = self.occurrence(start_year)
            if day in occurrence:
                return occurrence
        return None


@dataclass(frozen=True)
class Calendar:
    """The declared periods attached to one check, and the attached ids no row answered to."""

    entries: tuple[DeclaredPeriod, ...] = ()
    missing: tuple[uuid.UUID, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.entries or self.missing)

    def inside(self, day: date) -> tuple[DeclaredPeriod, Occurrence] | None:
        """The declared period ``day`` is in. With overlapping entries, the one that started
        most recently (the more specific), then the name."""
        found = [
            (entry, occurrence)
            for entry in self.entries
            if (occurrence := entry.containing(day)) is not None
        ]
        if not found:
            return None
        return max(found, key=lambda pair: (pair[1].start, pair[0].name.lower()))

    def earlier_occurrences(
        self, entry: DeclaredPeriod, occurrence: Occurrence
    ) -> list[Occurrence]:
        """The previous occurrences of ``entry``, newest first, up to the years of history
        kept. Recurring: the same range in the earlier years. Dated: the attached entries of
        the same series (case-insensitively) that ended before this one started."""
        if entry.kind == RECURRING:
            return [
                entry.occurrence(occurrence.start.year - years_back)
                for years_back in range(1, PERIOD_HISTORY_YEARS + 1)
            ]
        if entry.series is None:
            return []
        series = entry.series.lower()
        earlier = [
            Occurrence(other.start_date, other.end_date)
            for other in self.entries
            if other.kind == DATED
            and other.id != entry.id
            and (other.series or "").lower() == series
            and other.start_date is not None
            and other.end_date is not None
            and other.end_date < occurrence.start
        ]
        earlier.sort(key=lambda o: o.start, reverse=True)
        return earlier[:PERIOD_HISTORY_YEARS]


@dataclass(frozen=True)
class DeclaredEffect:
    """What a declared period did to one run's score."""

    entry: DeclaredPeriod
    occurrence: Occurrence
    offset_days: int
    #: The matching days in earlier occurrences the index came from.
    matched: tuple[date, ...] = ()
    #: No earlier occurrence in the history: a rise is expected and its size unknown.
    first_year: bool = False
    #: False when the first-year rule withheld the band (the value was above the level).
    banded: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "entry_id": str(self.entry.id),
            "name": self.entry.name,
            "kind": self.entry.kind,
            "occurrence": {
                "start": self.occurrence.start.isoformat(),
                "end": self.occurrence.end.isoformat(),
            },
            "offset_days": self.offset_days,
            "matched": [d.isoformat() for d in self.matched],
            "first_year": self.first_year,
            "banded": self.banded,
        }


@dataclass(frozen=True)
class YearlyScore:
    """A run scored with the yearly comparison, or the reason it was scored without."""

    applied: bool
    reason: str | None = None
    level: float | None = None
    index: float | None = None
    #: The earlier periods the index came from, newest first, and what each was against its
    #: own level then. Years that disagree widen the spread; the result shows them.
    matched: tuple[date, ...] = ()
    ratios: tuple[float, ...] = ()
    #: The level times the index: what this time of year is expected to be.
    expected: float | None = None
    z_score: float | None = None
    #: The centre and spread the run was scored against, in the metric's own units, so that
    #: ``|value - centre| / spread`` is the z-score.
    centre: float | None = None
    spread: float | None = None
    degenerate: bool = False
    #: Set when the run's day is inside a declared period.
    declared: DeclaredEffect | None = None
    #: Earlier points left out of the level for falling inside a declared period.
    excluded_from_level: int = 0
    #: ``runs`` for a whole-table metric: the level is its run-by-run baseline, each prior run
    #: first divided by its own day's index; the index comes from the one-a-day series.
    basis: str | None = None

    @property
    def banded(self) -> bool:
        return self.declared is None or self.declared.banded

    def as_dict(self) -> dict[str, Any]:
        if not self.applied:
            return {"applied": False, "reason": self.reason}
        return {
            "applied": True,
            "level": _rounded(self.level),
            "index": _rounded(self.index),
            "expected": _rounded(self.expected),
            "matched": [d.isoformat() for d in self.matched],
            "ratios": [round(r, 4) for r in self.ratios],
            "years": len(self.matched),
            **({"basis": self.basis} if self.basis else {}),
        }


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


class History:
    """A check's points by day, with the comparisons the yearly score needs."""

    def __init__(
        self, points: dict[date, float], params: AnomalyParams, calendar: Calendar | None = None
    ) -> None:
        self._value = points
        self._days = sorted(points)
        self._params = params
        self._weekly = params.period is not None and params.period.unit == "week"
        self._calendar = calendar or Calendar()
        # Which declared period each day is in, memoised: the level of every prior asks it
        # for every earlier day, and a calendar lookup walks every entry.
        self._inside: dict[date, tuple[DeclaredPeriod, Occurrence] | None] = {}
        self._candidates_of: dict[date, tuple[list[date], str, DeclaredEffect | None]] = {}
        self._factor_of: dict[date, float] = {}

    def _declared(self, day: date) -> tuple[DeclaredPeriod, Occurrence] | None:
        if not self._calendar.entries:
            return None
        if day not in self._inside:
            self._inside[day] = self._calendar.inside(day)
        return self._inside[day]

    def _ordinary(self, day: date) -> bool:
        return self._declared(day) is None

    def _earlier(self, day: date) -> list[date]:
        earlier = self._days[: bisect.bisect_left(self._days, day)]
        if self._params.seasonality:
            earlier = [d for d in earlier if d.weekday() == day.weekday()]
        return earlier

    def priors(self, day: date) -> list[date]:
        """The earlier periods ``day`` is ordinarily scored against, oldest first: the last
        ``window`` of them that fall in no declared period."""
        return [d for d in self._earlier(day) if self._ordinary(d)][-self._params.window :]

    def excluded_from_level(self, day: date) -> int:
        """How many of the last ``window`` earlier periods were left out for falling inside a
        declared period."""
        if not self._calendar.entries:
            return 0
        return sum(1 for d in self._earlier(day)[-self._params.window :] if not self._ordinary(d))

    def walk(self) -> Iterator[tuple[date, list[date]]]:
        """Every day with its ``priors``, oldest first, in one pass: what ``priors`` gives
        day by day, without rereading the history before each."""
        recent: dict[int, deque[date]] = {}
        for day in self._days:
            key = day.weekday() if self._params.seasonality else 0
            window = recent.setdefault(key, deque(maxlen=self._params.window))
            yield day, list(window)
            if self._ordinary(day):
                window.append(day)

    def level_of(self, priors: list[date]) -> float | None:
        if len(priors) < _LEVEL_MIN_POINTS:
            return None
        return statistics.median(self._value[d] for d in priors)

    def level(self, day: date) -> float | None:
        return self.level_of(self.priors(day))

    def _period_of(self, day: date) -> date:
        return day - timedelta(days=day.weekday()) if self._weekly else day

    def _candidates(self, day: date) -> tuple[list[date], str, DeclaredEffect | None]:
        """The earlier periods ``day`` may be compared with, newest first; the reason to give
        if none is usable; and the declared period it is in, if any."""
        if day not in self._candidates_of:
            self._candidates_of[day] = self._find_candidates(day)
        return self._candidates_of[day]

    def _find_candidates(self, day: date) -> tuple[list[date], str, DeclaredEffect | None]:
        declared = self._declared(day)
        if declared is None:
            # Last year's sale is not what an ordinary day looked like; but a year whose
            # matching day was ordinary and simply has no point is missing history.
            candidates = [
                then
                for years_back in range(1, PERIOD_HISTORY_YEARS + 1)
                if self._ordinary(then := matched_day(day, years_back, weekly=self._weekly))
            ]
            reason = NO_EARLIER_YEAR if candidates else EARLIER_YEAR_IN_DECLARED_PERIOD
            return candidates, reason, None
        entry, occurrence = declared
        offset = day - occurrence.start
        effect = DeclaredEffect(entry=entry, occurrence=occurrence, offset_days=offset.days)
        matched: list[date] = []
        for earlier in self._calendar.earlier_occurrences(entry, occurrence):
            # A week is the Monday it starts on, which can fall before an occurrence that
            # began mid-week: a match must lie inside the earlier occurrence on both sides.
            then = self._period_of(earlier.start + offset)
            if earlier.start <= then <= earlier.end:
                matched.append(then)
        return matched, NO_EARLIER_YEAR, effect

    def _ratios(self, day: date) -> tuple[list[float], list[date]] | str:
        """What each matching earlier period was against its own level, newest first, or the
        reason there is none."""
        candidates, reason, _ = self._candidates(day)
        ratios: list[float] = []
        matched: list[date] = []
        for then in candidates:
            if then not in self._value:
                continue
            level = self.level(then)
            if level is None:
                continue
            # A ratio means something only between non-negative amounts over a real level.
            if self._value[then] < 0 or level < 0:
                reason = NEGATIVE_VALUE
                continue
            if level == 0:
                reason = ZERO_LEVEL
                continue
            ratios.append(self._value[then] / level)
            matched.append(then)
        return (ratios, matched) if ratios else reason

    def index(self, day: date) -> tuple[float, tuple[date, ...]] | str:
        """``(index, matched periods)``, or the reason there is none. The index is the median
        of the earlier years' ratios: with two, their average."""
        found = self._ratios(day)
        if isinstance(found, str):
            return found
        return statistics.median(found[0]), tuple(found[1])

    def expected(self, day: date) -> float | None:
        """What ``day`` is expected to be: its level, times its index where one applies."""
        level = self.level(day)
        if level is None:
            return None
        index = self.index(day)
        return level * (1.0 if isinstance(index, str) else index[0])

    def score(self, day: date, value: float) -> YearlyScore:
        level = self.level(day)
        if level is None:
            return YearlyScore(applied=False, reason=INSUFFICIENT_HISTORY)
        if value < 0 or level < 0:
            return YearlyScore(applied=False, reason=NEGATIVE_VALUE)
        excluded = self.excluded_from_level(day)
        _, _, effect = self._candidates(day)
        found = self._ratios(day)
        if isinstance(found, str):
            if effect is None or found != NO_EARLIER_YEAR:
                # An earlier occurrence that exists but cannot size the period (a zero or
                # negative level then) is not a first year: the reason stands.
                return YearlyScore(applied=False, reason=found, declared=effect)
            # Inside a declared period with no earlier occurrence to size it by: a rise is
            # expected, and how large it should be is unknown. Above the level is not
            # banded; below it is judged as an ordinary day would be.
            effect = DeclaredEffect(
                entry=effect.entry,
                occurrence=effect.occurrence,
                offset_days=effect.offset_days,
                first_year=True,
                banded=value <= level,
            )
            ratios, matched = [1.0], []
        else:
            ratios, matched = found[0], list(found[1])
            if effect is not None:
                effect = DeclaredEffect(
                    entry=effect.entry,
                    occurrence=effect.occurrence,
                    offset_days=effect.offset_days,
                    matched=tuple(matched),
                )
        factor = statistics.median(ratios)
        expected = level * factor
        # Each prior's own distance from what was expected of it: the days after a sale are
        # then not judged against the sale, because the sale was expected too.
        priors = [
            (self._value[d], was) for d in self.priors(day) if (was := self.expected(d)) is not None
        ]
        proportional = expected > 0
        distances = [
            (got - was) / was if proportional else got - was
            for got, was in priors
            if was > 0 or not proportional
        ]
        if len(distances) < max(_LEVEL_MIN_POINTS, self._params.min_points):
            return YearlyScore(applied=False, reason=INSUFFICIENT_HISTORY)
        scale = expected if proportional else 1.0
        centre = expected + statistics.fmean(distances) * scale
        spread = statistics.stdev(distances) * scale
        if proportional and len(ratios) > 1:
            # The earlier years did not agree exactly on how big this time of year is; that
            # disagreement is uncertainty about what to expect, on top of ordinary noise.
            spread = math.hypot(spread, statistics.stdev(ratios) * level)
        # A series that repeats exactly leaves a spread of rounding error, not of data:
        # dividing by it would turn a difference in the fifteenth decimal into a verdict.
        negligible = _NEGLIGIBLE * max(1.0, abs(expected))
        degenerate = spread <= negligible
        if degenerate:
            spread = 0.0
            z_score = 0.0 if abs(value - centre) <= negligible else ANOMALY_DEGENERATE_Z
        else:
            z_score = abs(value - centre) / spread
        return YearlyScore(
            applied=True,
            level=level,
            index=factor,
            matched=tuple(matched),
            ratios=tuple(ratios),
            expected=expected,
            z_score=z_score,
            centre=centre,
            spread=spread,
            degenerate=degenerate,
            declared=effect,
            excluded_from_level=excluded,
        )

    def factor(self, day: date) -> float:
        """The index ``day`` is scaled by, or 1 where none applies."""
        if day not in self._factor_of:
            found = self.index(day)
            self._factor_of[day] = 1.0 if isinstance(found, str) else found[0]
        return self._factor_of[day]

    def score_runs(self, day: date, value: float, runs: list[tuple[date, float]]) -> YearlyScore:
        """A whole-table run (ADR 0048 decision 4, amended 2026-10-14): scored against its own
        run-by-run priors ``runs`` (the local day each was taken, its value), not against
        other days' one-a-day values. This history is the one-a-day series, and only supplies
        the index: each prior is divided by its day's index, and their mean and spread are
        multiplied by today's."""
        if value < 0:
            return YearlyScore(applied=False, reason=NEGATIVE_VALUE)
        _, _, effect = self._candidates(day)
        found = self._ratios(day)
        first_year = False
        if isinstance(found, str):
            if effect is None or found != NO_EARLIER_YEAR:
                return YearlyScore(applied=False, reason=found, declared=effect)
            first_year = True
            ratios, matched = [1.0], []
        else:
            ratios, matched = found[0], list(found[1])
        adjusted = [v / f for d, v in runs if (f := self.factor(d)) > 0]
        if len(adjusted) < max(_LEVEL_MIN_POINTS, self._params.min_points):
            return YearlyScore(applied=False, reason=INSUFFICIENT_HISTORY, declared=effect)
        if any(v < 0 for v in adjusted):
            return YearlyScore(applied=False, reason=NEGATIVE_VALUE, declared=effect)
        level = statistics.fmean(adjusted)
        noise = statistics.stdev(adjusted)
        factor = statistics.median(ratios)
        expected = level * factor
        spread = noise * factor if factor > 0 else noise
        if len(ratios) > 1:
            spread = math.hypot(spread, statistics.stdev(ratios) * level)
        if effect is not None:
            effect = DeclaredEffect(
                entry=effect.entry,
                occurrence=effect.occurrence,
                offset_days=effect.offset_days,
                matched=tuple(matched),
                first_year=first_year,
                banded=not first_year or value <= level,
            )
        negligible = _NEGLIGIBLE * max(1.0, abs(expected))
        degenerate = spread <= negligible
        if degenerate:
            spread = 0.0
            z_score = 0.0 if abs(value - expected) <= negligible else ANOMALY_DEGENERATE_Z
        else:
            z_score = abs(value - expected) / spread
        return YearlyScore(
            applied=True,
            level=level,
            index=factor,
            matched=tuple(matched),
            ratios=tuple(ratios),
            expected=expected,
            z_score=z_score,
            centre=expected,
            spread=spread,
            degenerate=degenerate,
            declared=effect,
            basis="runs",
        )


# ───────────────────────── detected periods (decision 5) ─────────────────────────

#: A period qualifies when its value is at least this many times its level.
DETECT_RATIO = 1.5
#: Consecutive qualifying periods a run needs: two days (one day is a spike, not a
#: period), one week.
DETECT_MIN_DAYS = 2
DETECT_MIN_WEEKS = 1
#: Two runs in different years are "the same time" when, laid on one year, they come
#: within this many days of each other: a sale that moves by up to a week still matches.
DETECT_SAME_TIME_DAYS = 7

SEEN_ONCE = "seen_once"
_RANGE_KEYS = ("start_month", "start_day", "end_month", "end_day")

# Why a detection had nothing to look at (``Detection.reason``).
NOT_PER_PERIOD = "not_a_per_period_metric"
NO_HISTORY = "no_history"
INVALID_CONFIG = "invalid_config"


@dataclass(frozen=True)
class DetectedOccurrence:
    """One run of qualifying periods: ``[start, end]`` inclusive (a week ends on Sunday)."""

    start: date
    end: date
    peak_ratio: float
    periods: int
    backfilled: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "year": self.start.year,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "peak_ratio": round(self.peak_ratio, 4),
            "periods": self.periods,
            "backfilled_periods": self.backfilled,
        }


@dataclass(frozen=True)
class DetectedPeriod:
    """Occurrences at the same time of year, oldest first. ``start`` / ``end`` span the most
    recent season (every occurrence in it: a sale split by an ordinary day is one season);
    ``range`` is the month/day span all seasons cover laid on one year, for a recurring
    one."""

    kind: str
    start: date
    end: date
    occurrences: tuple[DetectedOccurrence, ...]
    years_seen: int
    range: tuple[int, int, int, int] | None = None
    declared: tuple[DeclaredPeriod, ...] = ()

    @property
    def peak_ratio(self) -> float:
        return max(o.peak_ratio for o in self.occurrences)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "peak_ratio": round(self.peak_ratio, 4),
            "years_seen": self.years_seen,
            "range": (
                None if self.range is None else dict(zip(_RANGE_KEYS, self.range, strict=True))
            ),
            "occurrences": [o.as_dict() for o in self.occurrences],
            "declared": [{"id": str(e.id), "name": e.name} for e in self.declared],
        }


@dataclass(frozen=True)
class Detection:
    """What a check's history shows, or why it shows nothing."""

    periods: tuple[DetectedPeriod, ...] = ()
    reason: str | None = None
    unit: str | None = None
    #: How the level was taken: ``median`` with ``yearly`` on, ``mean`` without, as scoring.
    level: str | None = None
    points: int = 0
    backfilled: int = 0
    #: Periods with enough history before them to have a level.
    evaluated: int = 0
    first: date | None = None
    last: date | None = None

    @property
    def years_of_history(self) -> float:
        if self.first is None or self.last is None:
            return 0.0
        return round((self.last - self.first).days / 365.25, 2)

    def as_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "periods": [p.as_dict() for p in self.periods],
            "thresholds": {
                "ratio": DETECT_RATIO,
                "min_consecutive": (DETECT_MIN_WEEKS if self.unit == "week" else DETECT_MIN_DAYS),
                "same_time_days": DETECT_SAME_TIME_DAYS,
                "level": self.level,
            },
            "history": {
                "unit": self.unit,
                "points": self.points,
                "backfilled": self.backfilled,
                "evaluated": self.evaluated,
                "first": self.first.isoformat() if self.first else None,
                "last": self.last.isoformat() if self.last else None,
                "years": self.years_of_history,
            },
        }


def _laid_on(occ: DetectedOccurrence, anchor: date) -> tuple[int, date, date]:
    """``occ`` moved the whole number of years that lays it nearest ``anchor`` (zero when it
    is already the same season): the shift and the moved span."""
    best: tuple[int, date, date] | None = None
    for years in (anchor.year - occ.start.year + d for d in (-1, 0, 1)):
        start = month_day(occ.start.year + years, occ.start.month, occ.start.day)
        if best is None or abs((start - anchor).days) < abs((best[1] - anchor).days):
            best = (years, start, start + (occ.end - occ.start))
    assert best is not None
    return best


def _same_time(a: DetectedOccurrence, b: DetectedOccurrence) -> bool:
    _, start, end = _laid_on(b, a.start)
    tolerance = timedelta(days=DETECT_SAME_TIME_DAYS)
    return start <= a.end + tolerance and end >= a.start - tolerance


def overlapping(
    entries: tuple[DeclaredPeriod, ...], start: date, end: date
) -> list[DeclaredPeriod]:
    """The entries with an occurrence touching ``[start, end]``, by name."""
    days = [start + timedelta(days=n) for n in range((end - start).days + 1)]
    found = [e for e in entries if any(e.containing(d) is not None for d in days)]
    return sorted(found, key=lambda e: e.name.lower())


def _group(occurrences: list[DetectedOccurrence], declared: Calendar) -> list[DetectedPeriod]:
    parent = list(range(len(occurrences)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(occurrences):
        for j in range(i + 1, len(occurrences)):
            if _same_time(a, occurrences[j]):
                parent[root(j)] = root(i)
    groups: dict[int, list[DetectedOccurrence]] = {}
    for i, occ in enumerate(occurrences):
        groups.setdefault(root(i), []).append(occ)
    out: list[DetectedPeriod] = []
    for members in groups.values():
        members.sort(key=lambda o: o.start)
        anchor = members[-1]
        # Each occurrence laid on the newest one's season; two in one season (a sale split
        # by an ordinary day) are one year.
        laid = [(occ, *_laid_on(occ, anchor.start)) for occ in members]
        cycles = {years for _, years, _, _ in laid}
        latest = [occ for occ, years, _, _ in laid if years == 0]
        first = min(s for _, _, s, _ in laid)
        last = max(e for _, _, _, e in laid)
        recurring = len(cycles) >= 2
        start, end = min(o.start for o in latest), max(o.end for o in latest)
        out.append(
            DetectedPeriod(
                kind=RECURRING if recurring else SEEN_ONCE,
                start=start,
                end=end,
                occurrences=tuple(members),
                years_seen=len(cycles),
                range=(
                    (first.month, first.day, last.month, last.day)
                    if recurring and (last - first).days < 365
                    else None
                ),
                declared=tuple(
                    {
                        e.id: e
                        for occ in members
                        for e in overlapping(declared.entries, occ.start, occ.end)
                    }.values()
                ),
            )
        )
    out.sort(key=lambda p: p.start)
    return out


def detect(
    points: dict[date, float],
    params: AnomalyParams,
    declared: Calendar | None = None,
    *,
    backfilled: frozenset[date] = frozenset(),
) -> Detection:
    """The periods in a per-period check's history that ran at or above ``DETECT_RATIO``
    times their level. The level is taken over the priors scoring uses (``History.walk``:
    the ``window`` periods before, by weekday with ``seasonality``, attached declared periods
    left out) and the way scoring centres them: their median with ``yearly`` on
    (``History.level_of``), their mean without. A period is judged only with ``min_points``
    periods before it, the bar scoring sets. Runs of consecutive qualifying periods are
    occurrences; occurrences at the same time in different years are one detected period,
    ``recurring`` from two years and ``seen_once`` from one. A description: nothing here
    reaches a score."""
    if params.period is None:
        return Detection(reason=NOT_PER_PERIOD)
    unit = params.period.unit
    if not points:
        return Detection(reason=NO_HISTORY, unit=unit)
    declared = declared or Calendar()
    history = History(points, params, declared)
    step = params.period.length
    minimum = DETECT_MIN_WEEKS if unit == "week" else DETECT_MIN_DAYS
    occurrences: list[DetectedOccurrence] = []
    run: list[tuple[date, float]] = []
    evaluated = 0

    def close() -> None:
        if len(run) >= minimum:
            occurrences.append(
                DetectedOccurrence(
                    start=run[0][0],
                    end=run[-1][0] + step - timedelta(days=1),
                    peak_ratio=max(r for _, r in run),
                    periods=len(run),
                    backfilled=sum(1 for d, _ in run if d in backfilled),
                )
            )
        run.clear()

    for day, priors in history.walk():
        if run and run[-1][0] + step != day:
            close()
        ratio: float | None = None
        if len(priors) >= max(_LEVEL_MIN_POINTS, params.min_points):
            evaluated += 1
            level = (
                history.level_of(priors)
                if params.yearly
                else statistics.fmean(points[d] for d in priors)
            )
            if level is not None and level > 0 and points[day] >= 0:
                ratio = points[day] / level
        if ratio is not None and ratio >= DETECT_RATIO:
            run.append((day, ratio))
        else:
            close()
    close()
    days = sorted(points)
    return Detection(
        periods=tuple(_group(occurrences, declared)),
        reason=None if evaluated else INSUFFICIENT_HISTORY,
        unit=unit,
        level="median" if params.yearly else "mean",
        points=len(days),
        backfilled=sum(1 for d in days if d in backfilled),
        evaluated=evaluated,
        first=days[0],
        last=days[-1],
    )
