"""Yearly seasonality on the anomaly monitor: a seasonal index over the rolling baseline
(ADR 0048)."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.engine.default import DefaultDialect

from backend.app.datasources import monitors
from backend.app.datasources.monitors import MonitorConfigError
from backend.app.db.models import Check, Connection, Suite, User
from backend.app.services import anomaly, seasonal
from backend.app.services.monitor_baseline import get_baseline
from backend.app.services.seasonal import History, matched_day
from backend.tests.support.fake_secret_store import FakeSecretStore

_DAILY = {
    "target_metric": "period_row_count",
    "date_column": "ordered_at",
    "window": 14,
    "min_points": 7,
    "yearly": True,
}
# A sale that runs for the same seven days each November.
_SALE_2024 = (date(2024, 11, 25), date(2024, 12, 1))
_SALE_2025 = (date(2025, 11, 24), date(2025, 11, 30))


def _params(**overrides: Any) -> monitors.AnomalyParams:
    return monitors.anomaly_params({**_DAILY, **overrides})


def _series(
    start: date, end: date, *, sales: tuple[tuple[date, date], ...] = (), factor: float = 2.4
) -> dict[date, float]:
    """100 a day (110 on Saturdays), times ``factor`` inside a sale."""
    out: dict[date, float] = {}
    day = start
    while day < end:
        value = 110.0 if day.weekday() == 5 else 100.0
        if any(first <= day <= last for first, last in sales):
            value *= factor
        out[day] = value
        day += timedelta(days=1)
    return out


# ── config ───────────────────────────────────────────────────────────────────


def test_yearly_is_off_by_default_and_must_be_a_boolean() -> None:
    assert monitors.anomaly_params({**_DAILY, "yearly": None}).yearly is False
    assert monitors.anomaly_params({"target_metric": "row_count"}).yearly is False
    with pytest.raises(MonitorConfigError, match="yearly must be true or false"):
        monitors.anomaly_params({**_DAILY, "yearly": "yes"})


@pytest.mark.parametrize(
    "config",
    [
        {"target_metric": "row_count", "yearly": True},
        {"target_metric": "freshness_age_hours", "column": "loaded_at", "yearly": True},
        {"target_metric": "column_profile", "yearly": True},
    ],
)
def test_yearly_is_offered_on_the_per_period_metrics_only(config: dict[str, Any]) -> None:
    """A whole-table metric is measured several times a day and has no period to match
    across years."""
    with pytest.raises(MonitorConfigError, match="yearly is available only for"):
        monitors.anomaly_params(config)
    # Switched off, the key is harmless on any metric.
    assert monitors.anomaly_params({**config, "yearly": False}).yearly is False


# ── matching a period to an earlier year ─────────────────────────────────────


@pytest.mark.parametrize(
    ("day", "years", "expected"),
    [
        # Monday 12 Oct 2026 -> the Monday nearest 12 Oct 2025 (a Sunday) -> the 13th.
        (date(2026, 10, 12), 1, date(2025, 10, 13)),
        # ...and nearest 12 Oct 2024 (a Saturday) -> Monday the 14th.
        (date(2026, 10, 12), 2, date(2024, 10, 14)),
        # Thursday 1 Jan 2026 -> 1 Jan 2025 is a Wednesday -> Thursday the 2nd.
        (date(2026, 1, 1), 1, date(2025, 1, 2)),
        # Never more than three days from the calendar date, in either direction: three
        # years back, 12 Oct 2023 is a Thursday, and the nearest Monday is the 9th, not the 16th.
        (date(2026, 10, 12), 3, date(2023, 10, 9)),
        (date(2026, 3, 6), 1, date(2025, 3, 7)),
        (date(2027, 3, 1), 1, date(2026, 3, 2)),
        (date(2024, 3, 3), 1, date(2023, 3, 5)),
        # 29 February has no date a year back: taken from the 28th.
        (date(2024, 2, 29), 1, date(2023, 3, 2)),
    ],
)
def test_a_day_is_matched_to_the_same_weekday_nearest_the_same_date(
    day: date, years: int, expected: date
) -> None:
    got = matched_day(day, years, weekly=False)

    assert got == expected
    assert got.weekday() == day.weekday()


def test_a_week_is_matched_to_the_week_containing_that_date() -> None:
    # The week of Monday 28 Dec 2026 is ISO week 53; 2025 has no week 53.
    assert matched_day(date(2026, 12, 28), 1, weekly=True) == date(2025, 12, 22)
    assert matched_day(date(2026, 10, 12), 1, weekly=True) == date(2025, 10, 6)


# ── level, index, score ──────────────────────────────────────────────────────


def test_the_level_is_the_median_of_the_window_before_and_needs_two_points() -> None:
    points = {date(2026, 10, d): float(v) for d, v in ((1, 100), (2, 120), (3, 5000))}
    history = History(points, _params())

    assert history.level(date(2026, 10, 4)) == 120.0  # the median shrugs off one spike
    assert history.level(date(2026, 10, 3)) == 110.0
    assert history.level(date(2026, 10, 2)) is None


def test_the_index_is_what_last_year_ran_at_against_its_own_surroundings() -> None:
    history = History(
        _series(date(2024, 10, 1), date(2025, 11, 20), sales=(_SALE_2024,)), _params()
    )

    in_sale: Any = history.index(date(2025, 11, 26))  # a Wednesday, matched to Wed 27 Nov 2024
    ordinary: Any = history.index(date(2025, 10, 15))

    assert in_sale == (pytest.approx(2.4), (date(2024, 11, 27),))
    assert ordinary == (pytest.approx(1.0), (date(2024, 10, 16),))


def test_with_two_earlier_years_the_index_is_their_median() -> None:
    points = _series(date(2023, 10, 1), date(2025, 11, 20), sales=(_SALE_2024,))
    # The year before, the same sale ran at 2.0 times.
    points.update(
        _series(
            date(2023, 11, 27),
            date(2023, 12, 4),
            sales=((date(2023, 11, 27), date(2023, 12, 3)),),
            factor=2.0,
        )
    )

    index: Any = History(points, _params()).index(date(2025, 11, 26))

    assert index == (pytest.approx(2.2), (date(2024, 11, 27), date(2023, 11, 29)))


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda p: None, None),
        (lambda p: p.pop(date(2024, 11, 27)), seasonal.NO_EARLIER_YEAR),
        (lambda p: p.update({date(2024, 11, 27): -5.0}), seasonal.NEGATIVE_VALUE),
        (
            lambda p: p.update({d: 0.0 for d in list(p) if d < date(2024, 11, 27)}),
            seasonal.ZERO_LEVEL,
        ),
    ],
)
def test_the_index_says_why_there_is_none(mutate: Any, reason: str | None) -> None:
    points = _series(date(2024, 10, 1), date(2025, 11, 20), sales=(_SALE_2024,))
    mutate(points)

    index = History(points, _params()).index(date(2025, 11, 26))

    assert (index if isinstance(index, str) else None) == reason


def _history(until: date) -> History:
    return History(_series(date(2024, 10, 1), until, sales=(_SALE_2024, _SALE_2025)), _params())


def test_a_sale_that_arrives_as_last_year_is_normal_and_one_that_does_not_is_not() -> None:
    wednesday = date(2025, 11, 26)
    history = _history(wednesday)

    arrived = history.score(wednesday, 240.0)
    missing = history.score(wednesday, 100.0)

    assert arrived.applied and arrived.expected == pytest.approx(240.0)
    assert arrived.level == pytest.approx(100.0)
    assert arrived.index == pytest.approx(2.4)
    assert arrived.z_score is not None and arrived.z_score < 1
    # The load failed during the sale: flat is what the last fortnight looked like, and it
    # is nowhere near what this week should be.
    assert missing.z_score is not None and missing.z_score > 6


def _noisy_with_a_sale_day(
    sale: dict[int, float], *, start: date = date(2024, 10, 1)
) -> dict[date, float]:
    """About 1000 a day, give or take 3%, with one sale day each November."""
    points: dict[date, float] = {}
    day = start
    while day < date(2026, 11, 25):
        wobble = ((day.toordinal() * 37) % 61 - 30) / 1000  # deterministic, within 3%
        points[day] = 1000.0 * (1 + wobble)
        day += timedelta(days=1)
    for year, value in sale.items():
        points[matched_day(date(2026, 11, 25), 2026 - year, weekly=False)] = value
    return points


@pytest.mark.parametrize(
    ("value", "normal"),
    [
        (5000.0, True),  # as last year
        (4500.0, True),  # as the year before
        (5300.0, True),  # a little above either
        (1000.0, False),  # the sale did not arrive
        (9000.0, False),  # nearly double any sale it has seen
    ],
)
def test_a_peak_has_room_in_proportion_to_its_size_and_to_how_much_the_years_disagree(
    value: float, normal: bool
) -> None:
    """An ordinary day varies by 30. A sale day five times as large varies by more than
    that, and the two earlier years did not agree on its size either: a sale inside their
    range is not an anomaly."""
    history = History(_noisy_with_a_sale_day({2025: 5000.0, 2024: 4500.0}), _params())

    scored = history.score(date(2026, 11, 25), value)

    assert scored.applied and scored.index == pytest.approx(4.75, rel=0.03)
    assert scored.z_score is not None
    assert (scored.z_score < 3) is normal


def test_with_one_earlier_year_a_peak_is_allowed_its_own_share_of_ordinary_noise() -> None:
    """An ordinary day varies by under 3%, about 30. A 5000 sale day is allowed about 3% of
    5000, not 30: 2% above last year is noise, and 20% above is not."""
    history = History(_noisy_with_a_sale_day({2025: 5000.0}, start=date(2025, 10, 1)), _params())

    near = history.score(date(2026, 11, 25), 5100.0)
    far = history.score(date(2026, 11, 25), 6000.0)

    assert near.as_dict()["years"] == 1
    assert near.z_score is not None and near.z_score < 3
    assert far.z_score is not None and far.z_score > 3
    # A few percent of the peak, not the 20 to 30 an ordinary day moves by.
    assert near.spread is not None and 50 < near.spread < 200


def test_an_earlier_year_with_no_peak_makes_the_comparison_loose_and_the_result_shows_why() -> None:
    """Two years with a sale and one without do not agree on what this day should be. The
    check does not pretend they do: the spread is wide, and the three ratios are in the
    result for anyone asking why a low day passed."""
    points = _noisy_with_a_sale_day({2025: 5000.0, 2024: 4500.0})
    day = date(2023, 10, 1)
    while day < date(2024, 10, 1):
        points[day] = 1000.0
        day += timedelta(days=1)
    history = History(points, _params())

    scored = history.score(date(2026, 11, 25), 1000.0)

    assert [round(r, 1) for r in scored.as_dict()["ratios"]] == [5.0, 4.5, 1.0]
    assert scored.as_dict()["years"] == 3
    assert scored.z_score is not None and scored.z_score < 3


def test_the_centre_and_spread_reported_reproduce_the_score() -> None:
    history = History(_noisy_with_a_sale_day({2025: 5000.0, 2024: 4500.0}), _params())

    scored = history.score(date(2026, 11, 25), 5600.0)

    assert scored.centre is not None and scored.spread is not None and scored.z_score
    assert abs(5600.0 - scored.centre) / scored.spread == pytest.approx(scored.z_score)


def test_the_days_after_a_sale_are_not_judged_against_the_sale() -> None:
    after = date(2025, 12, 3)  # the Wednesday after the sale: back to 100
    history = _history(after)
    priors = [history._value[d] for d in history.priors(after)]

    normal = history.score(after, 100.0)
    dropped = history.score(after, 40.0)

    # The rolling window still holds the seven sale days.
    assert max(priors) == pytest.approx(264.0)
    assert normal.applied and normal.expected == pytest.approx(100.0)
    assert normal.z_score is not None and normal.z_score < 1
    # Each sale day was expected, so it adds no spread: a real drop the week after is still
    # caught, where a spread widened by the sale would have hidden it.
    assert dropped.z_score is not None and dropped.z_score > 6


def test_a_day_the_shop_was_closed_last_year_expects_zero_without_dividing_by_it() -> None:
    day = date(2026, 11, 25)
    history = History(_noisy_with_a_sale_day({2025: 0.0}, start=date(2025, 10, 1)), _params())

    closed_again = history.score(day, 0.0)
    a_few_orders = history.score(day, 20.0)
    open_this_year = history.score(day, 1000.0)

    assert (closed_again.index, closed_again.expected) == (0.0, 0.0)
    assert closed_again.z_score is not None and closed_again.z_score < 1
    # With nothing to take a proportion of, the distance is taken as it stands, against the
    # ordinary day-to-day spread (about 17 here): a few orders are noise, a normal day's
    # worth is not.
    assert a_few_orders.z_score is not None and a_few_orders.z_score < 3
    assert open_this_year.z_score is not None and open_this_year.z_score > 6


@pytest.mark.parametrize(
    ("points", "day", "value", "reason"),
    [
        ({date(2026, 10, 1): 100.0}, date(2026, 10, 2), 100.0, seasonal.INSUFFICIENT_HISTORY),
        (
            _series(date(2025, 10, 1), date(2025, 11, 26)),
            date(2025, 11, 26),
            100.0,
            seasonal.NO_EARLIER_YEAR,
        ),
        (
            _series(date(2024, 10, 1), date(2025, 11, 26)),
            date(2025, 11, 26),
            -1.0,
            seasonal.NEGATIVE_VALUE,
        ),
    ],
)
def test_the_yearly_comparison_says_why_it_was_not_applied(
    points: dict[date, float], day: date, value: float, reason: str
) -> None:
    scored = History(points, _params()).score(day, value)

    assert scored.as_dict() == {"applied": False, "reason": reason}


def test_a_history_with_no_spread_scores_zero_or_the_fixed_degenerate_value() -> None:
    points = dict.fromkeys(_series(date(2024, 10, 1), date(2025, 11, 26)), 100.0)
    history = History(points, _params())

    same = history.score(date(2025, 11, 26), 100.0)
    moved = history.score(date(2025, 11, 26), 101.0)

    assert (same.z_score, same.degenerate) == (0.0, True)
    assert moved.z_score == monitors.ANOMALY_DEGENERATE_Z


# ── through the executor ─────────────────────────────────────────────────────


def _at(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=UTC)


def _connection() -> Connection:
    return Connection(
        id=uuid.uuid4(),
        name="pg",
        type="postgres",
        env="dev",
        config={"host": "h", "database": "shop", "user": "u"},
        secret_ref="ref",
        created_by=uuid.uuid4(),
    )


@pytest.fixture
def graph(db_session: Any) -> tuple[Any, Connection, Check]:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@x.io")
    db_session.add(user)
    db_session.flush()
    conn = _connection()
    conn.created_by = user.id
    db_session.add(conn)
    db_session.flush()
    suite = Suite(
        name=f"s-{uuid.uuid4().hex[:8]}",
        connection_id=conn.id,
        created_by=user.id,
        target={"schema": "shop", "table": "orders"},
    )
    db_session.add(suite)
    db_session.flush()
    check = Check(
        suite_id=suite.id,
        name="daily orders",
        kind="anomaly",
        expectation_type="monitor:anomaly",
        config=dict(_DAILY),
        fail_threshold=Decimal("3"),
    )
    db_session.add(check)
    db_session.flush()
    return db_session, conn, check


def _run(
    graph: tuple[Any, Connection, Check],
    monkeypatch: pytest.MonkeyPatch,
    now: str,
    value: float,
    history: dict[date, float] | None = None,
) -> Any:
    session, conn, check = graph

    class _Res:
        def scalar(self) -> Any:
            return value

        def all(self) -> Any:
            return list((history or {}).items())

    class _Conn:
        dialect = DefaultDialect()

        def execute(self, statement: Any) -> Any:
            return _Res()

    @contextmanager
    def fake_open(connection: Connection, secret_store: Any) -> Any:
        yield _Conn()

    monkeypatch.setattr(anomaly, "_open_connection", fake_open)
    outcome = anomaly.build_anomaly_executor(
        session,
        connection=conn,
        target_table="orders",
        target_schema="shop",
        target_catalog=None,
        secret_store=FakeSecretStore(default="secret", raise_on_write=True),
        clock=lambda: _at(now),
    )(check)
    session.flush()
    return outcome


def test_a_seeded_check_judges_its_first_sale_against_last_years(
    graph: tuple[Any, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The table holds last year's sale, so the check's first run, in this year's sale, is
    not a false alarm."""
    table = _series(date(2024, 10, 1), date(2025, 11, 26), sales=(_SALE_2024, _SALE_2025))

    outcome = _run(graph, monkeypatch, "2025-11-27T12:00", 240, table)  # judges Wed the 26th

    observed = outcome.observed_value
    assert observed["yearly"] == {
        "applied": True,
        "level": 100.0,
        "index": 2.4,
        "expected": 240.0,
        "matched": ["2024-11-27"],
        "ratios": [2.4],
        "years": 1,
    }
    # `mean` and `stddev` are what the run was scored against. This table repeats exactly
    # (last year's Saturdays explain this year's), so nothing is left over as spread.
    assert (observed["mean"], observed["deviation"], observed["stddev"]) == (240.0, 0.0, 0.0)
    assert observed["degenerate_stddev"] is True
    assert outcome.metric_value is not None and outcome.metric_value < 1
    assert outcome.expected_value["yearly"] is True


def test_the_same_run_without_yearly_is_a_false_alarm(
    graph: tuple[Any, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, _, check = graph
    check.config = {**_DAILY, "yearly": False}
    session.flush()
    table = _series(date(2024, 10, 1), date(2025, 11, 24), sales=(_SALE_2024,))

    outcome = _run(graph, monkeypatch, "2025-11-25T12:00", 240, table)  # the sale's first day

    assert "yearly" not in outcome.observed_value
    assert outcome.metric_value is not None and outcome.metric_value > 6


def test_with_no_earlier_year_the_score_is_exactly_the_ordinary_one(
    graph: tuple[Any, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, _, check = graph
    table = _series(date(2025, 10, 1), date(2025, 11, 24))
    with_yearly = _run(graph, monkeypatch, "2025-11-25T12:00", 180, table)

    row = get_baseline(session, check.id)
    assert row is not None
    session.delete(row)
    check.config = {**_DAILY, "yearly": False}
    session.flush()
    without = _run(graph, monkeypatch, "2025-11-25T12:00", 180, table)

    assert with_yearly.observed_value["yearly"] == {"applied": False, "reason": "no_earlier_year"}
    keys = ("z_score", "mean", "stddev", "deviation", "points")
    assert [with_yearly.observed_value[k] for k in keys] == [
        without.observed_value[k] for k in keys
    ]
