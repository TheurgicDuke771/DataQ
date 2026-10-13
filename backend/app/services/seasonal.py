"""Yearly seasonality for the anomaly monitor (ADR 0048): a seasonal index over the rolling
baseline.

Pure functions over a check's stored points. The *level* at a period is the median of the
points it would ordinarily be scored against; the *index* is what the matching period in an
earlier year was, divided by the level then; the *expected* value is the level now times the
index. A run is scored on how far it is from that **in proportion to it**, against how far
its prior points were from what was expected of them: a sale day five times an ordinary day
has five times the room. Where the expected value is zero (the shop was closed that day
last year) there is no proportion to take, and the distance is taken as it stands.
"""

from __future__ import annotations

import math
import statistics
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
        }


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


class History:
    """A check's points by day, with the comparisons the yearly score needs."""

    def __init__(self, points: dict[date, float], params: AnomalyParams) -> None:
        self._value = points
        self._days = sorted(points)
        self._params = params
        self._weekly = params.period is not None and params.period.unit == "week"

    def priors(self, day: date) -> list[date]:
        """The earlier periods ``day`` is ordinarily scored against, oldest first."""
        earlier = [d for d in self._days if d < day]
        if self._params.seasonality:
            earlier = [d for d in earlier if d.weekday() == day.weekday()]
        return earlier[-self._params.window :]

    def level(self, day: date) -> float | None:
        priors = self.priors(day)
        if len(priors) < _LEVEL_MIN_POINTS:
            return None
        return statistics.median(self._value[d] for d in priors)

    def _ratios(self, day: date) -> tuple[list[float], list[date]] | str:
        """What each matching earlier period was against its own level, newest first, or the
        reason there is none."""
        ratios: list[float] = []
        matched: list[date] = []
        reason = NO_EARLIER_YEAR
        for years_back in range(1, PERIOD_HISTORY_YEARS + 1):
            then = matched_day(day, years_back, weekly=self._weekly)
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
        found = self._ratios(day)
        if isinstance(found, str):
            return YearlyScore(applied=False, reason=found)
        ratios, matched = found
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
        )
