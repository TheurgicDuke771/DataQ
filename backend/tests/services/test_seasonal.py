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
from backend.app.db.models import Check, Connection, MonitorBaseline, Suite, User
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
    assert monitors.anomaly_params({"target_metric": "row_count"}).yearly is False
    assert monitors.anomaly_params({"target_metric": "row_count", "yearly": None}).yearly is False
    with pytest.raises(MonitorConfigError, match="yearly must be true or false"):
        monitors.anomaly_params({"target_metric": "row_count", "yearly": "yes"})


def test_yearly_is_refused_for_the_column_profile() -> None:
    with pytest.raises(
        MonitorConfigError, match="not available for target_metric='column_profile'"
    ):
        monitors.anomaly_params({"target_metric": "column_profile", "yearly": True})


@pytest.mark.parametrize(
    ("config", "kept"),
    [
        ({"target_metric": "row_count", "window": 14, "yearly": True}, 14 + 3 * 366),
        (
            {"target_metric": "freshness_age_hours", "column": "t", "window": 14, "yearly": True},
            1112,
        ),
        ({"target_metric": "row_count", "window": 14}, 14),
        (_DAILY, 14 + 3 * 366),  # a per-period check keeps its years whether or not it is on
        ({**_DAILY, "yearly": False}, 14 + 3 * 366),
    ],
)
def test_a_whole_table_check_keeps_three_years_only_with_yearly_on(
    config: dict[str, Any], kept: int
) -> None:
    assert monitors.anomaly_params(config).retained_observations == kept


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
    points = _series(date(2024, 10, 1), date(2025, 12, 25))
    points[date(2024, 12, 26)] = 0.0  # Thursday 26 Dec 2024, matched from Thu 25 Dec 2025
    history = History(points, _params())

    closed_again = history.score(date(2025, 12, 25), 0.0)
    open_this_year = history.score(date(2025, 12, 25), 100.0)

    assert (closed_again.index, closed_again.expected) == (0.0, 0.0)
    assert closed_again.z_score is not None and closed_again.z_score < 1
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
        "years": 1,
    }
    # `mean` is what was expected, so the band every surface draws is the seasonal one.
    assert (observed["mean"], observed["deviation"]) == (240.0, 0.0)
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


def test_a_whole_table_check_with_yearly_keeps_one_point_a_day_the_days_last(
    graph: tuple[Any, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, _, check = graph
    check.config = {"target_metric": "row_count", "window": 5, "min_points": 3, "yearly": True}
    session.flush()
    # Points it stored, several a day, before `yearly` was switched on.
    session.add(
        MonitorBaseline(
            check_id=check.id,
            kind="anomaly",
            baseline={
                "version": 1,
                "target_metric": "row_count",
                "window": 5,
                "seasonality": False,
                "observations": [
                    {"ts": "2026-10-10T06:00:00+00:00", "value": 100},
                    {"ts": "2026-10-10T18:00:00+00:00", "value": 105},
                    {"ts": "2026-10-11T06:00:00+00:00", "value": 110},
                ],
            },
        )
    )
    session.flush()

    _run(graph, monkeypatch, "2026-10-12T06:00", 120)
    second = _run(graph, monkeypatch, "2026-10-12T18:00", 125)

    row = get_baseline(session, check.id)
    assert row is not None
    assert [(o["ts"][:10], o["value"]) for o in row.baseline["observations"]] == [
        ("2026-10-10", 105.0),
        ("2026-10-11", 110.0),
        ("2026-10-12", 125.0),
    ]
    # Scored against earlier days only, never against this morning's run.
    assert second.observed_value["points"] == 2
    assert "period" not in second.observed_value
