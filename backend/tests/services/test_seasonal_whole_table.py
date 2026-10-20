"""Yearly seasonality on the whole-table anomaly metrics (ADR 0048 decision 4, amended): a
one-a-day series beside the run-by-run points, used only for the yearly index."""

from __future__ import annotations

import random
import statistics
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from backend.app.datasources import monitors
from backend.app.datasources.monitors import MonitorConfigError
from backend.app.db.models import Check, Connection, Suite, User
from backend.app.services import anomaly
from backend.app.services.anomaly import Daily, DailyPoint, Observation
from backend.app.services.monitor_baseline import get_baseline, insert_baseline_if_absent
from backend.app.services.seasonal import Calendar, DeclaredPeriod, History
from backend.tests.support.fake_secret_store import FakeSecretStore

_ROWS = {"target_metric": "row_count", "window": 14, "min_points": 7, "yearly": True}
_AGE = {
    "target_metric": "freshness_age_hours",
    "column": "loaded_at",
    "window": 24,
    "min_points": 7,
    "yearly": True,
}


def _params(config: dict[str, Any], **overrides: Any) -> monitors.AnomalyParams:
    return monitors.anomaly_params({**config, **overrides})


def _in_peak(day: date) -> bool:
    """A snapshot table that swells for sixteen days every year from the Thursday nearest 20
    November (a day is matched to the same weekday a year back, so the peak lines up)."""
    base = date(day.year, 11, 20)
    shift = (3 - base.weekday()) % 7
    start = base + timedelta(days=shift - 7 if shift > 3 else shift)
    return start <= day < start + timedelta(days=16)


def _daily_rows(start: date, end: date, *, seed: int = 7) -> dict[date, float]:
    """About 1000 rows (give or take 3%, not repeating), 2.4 times that in the peak."""
    rng = random.Random(seed)
    out: dict[date, float] = {}
    day = start
    while day < end:
        value = 1000.0 * (1 + rng.gauss(0, 0.03))
        out[day] = value * 2.4 if _in_peak(day) else value
        day += timedelta(days=1)
    return out


def _hourly(
    start: datetime, hours: int, value: Any, *, seed: int = 11
) -> list[tuple[datetime, float]]:
    rng = random.Random(seed)
    return [
        (start + timedelta(hours=h), value(start + timedelta(hours=h), rng)) for h in range(hours)
    ]


def _rows_at(ts: datetime, rng: random.Random) -> float:
    base = 1000.0 * (1 + rng.gauss(0, 0.03))
    return base * 2.4 if _in_peak(ts.date()) else base


# ── config ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("config", [_ROWS, _AGE])
def test_yearly_is_accepted_on_the_whole_table_metrics(config: dict[str, Any]) -> None:
    params = monitors.anomaly_params(config, authoring=True)

    assert params.yearly is True
    assert params.period is None
    assert params.timezone == "UTC"


def test_a_whole_table_check_keeps_its_days_in_its_own_time_zone_only_with_yearly() -> None:
    assert _params(_ROWS, timezone="Europe/London").timezone == "Europe/London"
    with pytest.raises(MonitorConfigError, match="IANA time zone"):
        _params(_ROWS, timezone="europe/london")
    with pytest.raises(MonitorConfigError, match="'row_count' only with yearly: true"):
        monitors.anomaly_params({**_ROWS, "yearly": False, "timezone": "UTC"}, authoring=True)
    # A per-period key that has nothing to do with a whole table is still refused.
    with pytest.raises(MonitorConfigError, match="applies only to target_metric"):
        monitors.anomaly_params({**_ROWS, "date_column": "day"}, authoring=True)


def test_a_stored_check_whose_yearly_was_switched_off_keeps_running() -> None:
    """An inheriting automatic check can have ``yearly`` switched off under it (ADR 0047):
    its time zone and attachments lie idle instead of failing every run."""
    stored = {
        **_ROWS,
        "yearly": False,
        "timezone": "Asia/Tokyo",
        "seasonal_periods": [str(uuid.uuid4())],
    }

    params = monitors.anomaly_params(stored)

    assert (params.yearly, params.timezone, params.seasonal_periods) == (False, "UTC", ())


def test_the_outcome_names_the_zone_the_days_are_kept_in() -> None:
    outcome = monitors._anomaly_outcome(
        {"z_score": 1.0}, {**_AGE, "timezone": "Asia/Tokyo"}, datetime.now(UTC)
    )

    assert outcome.expected_value is not None
    assert outcome.expected_value["yearly"] is True
    assert outcome.expected_value["timezone"] == "Asia/Tokyo"


# ── the one-a-day series ─────────────────────────────────────────────────────


def test_a_day_is_the_median_of_its_runs_and_closes_when_the_next_begins() -> None:
    params = _params(_ROWS)
    daily = None
    for value in (10.0, 50.0, 12.0, 11.0):
        daily = anomaly.record_daily(daily, date(2026, 3, 1), value, params)
    assert daily is not None
    (day,) = daily.points
    # One bad run (50) does not move the day; a mean would have.
    assert (day.value, day.runs) == (11.5, 4)

    daily = anomaly.record_daily(daily, date(2026, 3, 2), 20.0, params)

    first, second = daily.points
    assert first.values is None and (first.value, first.runs) == (11.5, 4)
    assert (second.value, second.runs, second.values) == (20.0, 1, (20.0,))


def test_a_run_dated_before_the_newest_day_changes_nothing() -> None:
    params = _params(_ROWS)
    daily = anomaly.record_daily(None, date(2026, 3, 2), 20.0, params)

    again = anomaly.record_daily(daily, date(2026, 3, 1), 99.0, params)

    assert again.points == daily.points


def test_a_day_keeps_a_bounded_number_of_runs() -> None:
    params = _params(_ROWS)
    daily = None
    for n in range(anomaly.DAILY_RUNS_KEPT + 50):
        daily = anomaly.record_daily(daily, date(2026, 3, 1), float(n), params)
    assert daily is not None
    (day,) = daily.points
    assert day.runs == anomaly.DAILY_RUNS_KEPT + 50
    assert day.values is not None and len(day.values) == anomaly.DAILY_RUNS_KEPT


def test_the_daily_series_keeps_three_years_and_its_window() -> None:
    params = _params(_ROWS, seasonality=True)
    today = date(2026, 6, 1)
    kept = monitors.PERIOD_HISTORY_YEARS * 366 + 14 * 7
    old = Daily(
        timezone="UTC",
        points=tuple(
            DailyPoint(today - timedelta(days=n), 1.0, 1) for n in range(kept + 30, 0, -1)
        ),
    )

    trimmed = anomaly.record_daily(old, today, 1.0, params)

    assert trimmed.points[0].day == today - timedelta(days=kept)
    assert trimmed.points[-1].day == today


def test_the_day_of_a_run_is_taken_in_the_check_s_zone() -> None:
    late = datetime(2026, 3, 1, 23, 30, tzinfo=UTC)

    assert anomaly.local_day(late, "UTC") == date(2026, 3, 1)
    assert anomaly.local_day(late, "Asia/Tokyo") == date(2026, 3, 2)


# ── scoring ─────────────────────────────────────────────────────────────────


def _score(
    params: monitors.AnomalyParams,
    daily: dict[date, float],
    runs: list[tuple[datetime, float]],
    today: date,
    value: float,
    calendar: Calendar | None = None,
) -> Any:
    observations = [Observation(ts=ts, value=v) for ts, v in runs]
    now = datetime.combine(today, datetime.min.time(), tzinfo=UTC) + timedelta(hours=12)
    return anomaly.whole_table_yearly(
        {d: v for d, v in daily.items() if d < today},
        params,
        calendar,
        today=today,
        value=value,
        observations=observations,
        now=now,
    )


def test_a_peak_that_came_in_earlier_years_is_expected_and_one_that_does_not_come_is_flagged() -> (
    None
):
    params = _params(_ROWS)
    today = date(2025, 11, 20)  # the first day of this year's peak
    daily = _daily_rows(date(2023, 9, 1), today)
    runs = _hourly(datetime(2025, 11, 19, tzinfo=UTC), 24, _rows_at)

    arrived, _ = _score(params, daily, runs, today, 2400.0)
    missing, _ = _score(params, daily, runs, today, 1000.0)

    assert arrived.applied and len(arrived.matched) == 2
    assert arrived.index == pytest.approx(2.4, rel=0.06)
    assert arrived.level == pytest.approx(1000, rel=0.03)
    assert arrived.z_score is not None and arrived.z_score < 3
    assert missing.z_score is not None and missing.z_score > 6
    # Without the yearly comparison the peak itself is the alarm.
    ordinary, *_ = anomaly.score(2400.0, [v for _, v in runs[-14:]])
    assert ordinary > 20


def test_runs_inside_the_peak_are_scaled_back_by_their_own_day_s_index() -> None:
    """Day three of the peak: every prior run is a peak run. Scored against them raw, and
    then multiplied by the index again, a normal peak day would read as far too low."""
    params = _params(_ROWS)
    today = date(2025, 11, 22)
    daily = _daily_rows(date(2023, 9, 1), today)
    runs = _hourly(datetime(2025, 11, 21, tzinfo=UTC), 24, _rows_at)

    scored, _ = _score(params, daily, runs, today, 2400.0)

    assert scored.applied
    assert scored.level == pytest.approx(1000, rel=0.04)
    assert scored.expected == pytest.approx(2400, rel=0.06)
    assert scored.z_score is not None and scored.z_score < 3


def test_an_hourly_check_is_scored_against_its_runs_not_against_other_days() -> None:
    """The trap the first build fell into (ADR 0048, amended 2026-10-14): a table loaded once a
    day at 02:00 and checked hourly reads one hour old at 03:00 and twenty-one at 23:00. A
    one-hour reading is normal; scored against earlier days' single values it is not."""
    params = _params(_AGE)
    today = date(2025, 6, 10)
    rng = random.Random(3)
    daily = {
        date(2023, 5, 1) + timedelta(days=n): 11.5 * (1 + rng.gauss(0, 0.02))
        for n in range((today - date(2023, 5, 1)).days)
    }

    def age(ts: datetime, r: random.Random) -> float:
        return (ts.hour - 2) % 24 + r.uniform(0, 0.2)

    runs = _hourly(datetime(2025, 6, 9, 3, tzinfo=UTC), 24, age)

    scored, _ = _score(params, daily, runs, today, 1.0)

    assert scored.applied and scored.basis == "runs"
    assert scored.z_score is not None and scored.z_score < 3
    assert scored.level == pytest.approx(11.6, rel=0.05)


def test_with_no_earlier_year_the_comparison_says_so() -> None:
    params = _params(_ROWS)
    today = date(2025, 11, 20)
    daily = _daily_rows(date(2025, 6, 1), today)
    runs = _hourly(datetime(2025, 11, 19, tzinfo=UTC), 24, _rows_at)

    scored, without = _score(params, daily, runs, today, 1000.0)

    assert (scored.applied, scored.reason) == (False, "no_earlier_year")
    assert without is None


def _entry(start: date, end: date) -> DeclaredPeriod:
    return DeclaredPeriod(
        id=uuid.uuid4(), name="Launch", kind="dated", start_date=start, end_date=end, series="L"
    )


def test_a_declared_period_with_no_earlier_occurrence_does_not_band_a_rise() -> None:
    params = _params(_ROWS)
    today = date(2025, 3, 12)
    daily = _daily_rows(date(2023, 9, 1), today)

    def launch(ts: datetime, rng: random.Random) -> float:
        return _rows_at(ts, rng) * (3 if ts.date() == date(2025, 3, 11) else 1)

    runs = _hourly(datetime(2025, 3, 10, tzinfo=UTC), 48, launch)
    calendar = Calendar(entries=(_entry(date(2025, 3, 11), date(2025, 3, 15)),))

    rise, without = _score(params, daily, runs, today, 3000.0, calendar)
    drop, _ = _score(params, daily, runs, today, 400.0, calendar)

    assert rise.applied and rise.declared is not None and rise.declared.first_year
    assert rise.banded is False
    # The runs on 11 March are inside the period: left out of the level, which is March 10's.
    assert rise.excluded_from_level == 14
    assert rise.level == pytest.approx(1000, rel=0.04)
    assert without is not None and without.declared is None
    assert drop.banded is True and drop.z_score is not None and drop.z_score > 6


def test_years_that_disagree_about_the_peak_widen_the_spread() -> None:
    params = _params(_ROWS)
    today = date(2025, 11, 22)
    daily = _daily_rows(date(2023, 9, 1), today)
    for day in list(daily):
        if day.year == 2023 and _in_peak(day):
            daily[day] *= 1.5  # a peak of 3.6 then, 2.4 last year
    runs = _hourly(datetime(2025, 11, 21, tzinfo=UTC), 24, _rows_at)

    scored, _ = _score(params, daily, runs, today, 3000.0)

    assert scored.applied and len(scored.ratios) == 2
    disagreement = statistics.stdev(scored.ratios) * scored.level
    assert disagreement > 300
    assert scored.spread is not None and scored.spread >= disagreement


def test_the_index_is_the_same_one_the_per_period_history_gives() -> None:
    params = _params(_ROWS)
    today = date(2025, 11, 22)
    daily = _daily_rows(date(2023, 9, 1), today)

    assert History(daily, params).factor(today) == pytest.approx(2.4, rel=0.06)
    assert History(daily, params).factor(date(2025, 10, 1)) == pytest.approx(1.0, rel=0.06)


# ── through the executor ─────────────────────────────────────────────────────


@pytest.fixture
def graph(db_session: Any) -> tuple[Any, Connection, Check]:
    user = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@x.io")
    db_session.add(user)
    db_session.flush()
    conn = Connection(
        name="pg",
        type="postgres",
        env="dev",
        config={"host": "h", "database": "shop", "user": "u"},
        secret_ref="ref",
        created_by=user.id,
    )
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
        name="orders volume",
        kind="anomaly",
        expectation_type="monitor:anomaly",
        config=dict(_ROWS),
        fail_threshold=Decimal("3"),
    )
    db_session.add(check)
    db_session.flush()
    return db_session, conn, check


def _run(
    graph: tuple[Any, Connection, Check],
    monkeypatch: pytest.MonkeyPatch,
    now: datetime,
    value: float,
) -> Any:
    session, conn, check = graph
    monkeypatch.setattr(anomaly, "measure_metric", lambda *a, **k: value)
    outcome = anomaly.build_anomaly_executor(
        session,
        connection=conn,
        target_table="orders",
        target_schema="shop",
        target_catalog=None,
        secret_store=FakeSecretStore(default="secret", raise_on_write=True),
        clock=lambda: now,
    )(check)
    session.flush()
    return outcome


def _seed_baseline(
    session: Any, check: Check, runs: list[tuple[datetime, float]], daily: dict[date, float]
) -> None:
    params = monitors.anomaly_params(check.config)
    payload = anomaly.dump_baseline(
        [Observation(ts=ts, value=v) for ts, v in runs],
        params,
        daily=Daily(
            timezone="UTC",
            points=tuple(DailyPoint(d, v, 24) for d, v in sorted(daily.items())),
        ),
    )
    insert_baseline_if_absent(session, check_id=check.id, kind="anomaly", baseline=payload)
    session.flush()


def _stored(session: Any, check: Check) -> dict[str, Any]:
    row = get_baseline(session, check.id)
    assert row is not None
    session.refresh(row)
    return dict(row.baseline)


def test_a_run_in_the_peak_reports_every_number_in_the_chain(
    graph: tuple[Any, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, _, check = graph
    today = date(2025, 11, 20)
    _seed_baseline(
        session,
        check,
        _hourly(datetime(2025, 11, 19, 10, tzinfo=UTC), 14, _rows_at),
        _daily_rows(date(2023, 9, 1), today),
    )

    outcome = _run(graph, monkeypatch, datetime(2025, 11, 20, 1, tzinfo=UTC), 2400.0)

    observed = outcome.observed_value
    yearly = observed["yearly"]
    assert yearly["applied"] is True and yearly["basis"] == "runs"
    assert yearly["years"] == 2 and len(yearly["ratios"]) == 2
    assert yearly["expected"] == pytest.approx(yearly["level"] * yearly["index"], rel=1e-4)
    assert observed["daily"] == {
        "timezone": "UTC",
        "days": len(_daily_rows(date(2023, 9, 1), today)),
        "first": "2023-09-01",
    }
    assert observed["points"] == 14
    # `mean` / `stddev` are what the run was scored against.
    assert abs(2400.0 - observed["mean"]) / observed["stddev"] == pytest.approx(
        observed["z_score"], rel=1e-4
    )
    assert outcome.metric_value is not None and outcome.metric_value < 3


def test_until_a_year_accumulates_the_score_is_the_ordinary_one_and_says_why(
    graph: tuple[Any, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, check = graph
    start = datetime(2026, 3, 1, tzinfo=UTC)
    for h in range(12):
        _run(graph, monkeypatch, start + timedelta(hours=h), 1000.0 + (h % 3) * 10)

    outcome = _run(graph, monkeypatch, start + timedelta(hours=13), 1010.0)

    observed = outcome.observed_value
    assert observed["yearly"] == {"applied": False, "reason": "no_earlier_year"}
    assert observed["daily"]["days"] == 0  # today is not history yet
    ordinary = anomaly.build_score_payload(
        1010.0, [1000.0 + (h % 3) * 10 for h in range(12)], monitors.anomaly_params(check.config)
    )
    assert observed["z_score"] == ordinary["z_score"]


def test_switching_yearly_off_and_on_keeps_both_series(
    graph: tuple[Any, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, _, check = graph
    day_one = datetime(2026, 3, 1, 9, tzinfo=UTC)
    for h in range(3):
        _run(graph, monkeypatch, day_one + timedelta(hours=h), 1000.0 + h)
    on = _stored(session, check)
    assert [p["day"] for p in on["daily"]["points"]] == ["2026-03-01"]

    check.config = {**_ROWS, "yearly": False}
    session.flush()
    _run(graph, monkeypatch, day_one + timedelta(days=1), 990.0)
    off = _stored(session, check)

    assert off["daily"] == on["daily"]
    assert len(off["observations"]) == 4

    check.config = dict(_ROWS)
    session.flush()
    _run(graph, monkeypatch, day_one + timedelta(days=2), 1005.0)
    again = _stored(session, check)

    assert [p["day"] for p in again["daily"]["points"]] == ["2026-03-01", "2026-03-03"]
    assert again["daily"]["points"][0]["value"] == 1001.0
    assert len(again["observations"]) == 5


def test_only_the_daily_series_keeps_years(
    graph: tuple[Any, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, _, check = graph
    today = date(2026, 6, 1)
    _seed_baseline(
        session,
        check,
        _hourly(datetime(2026, 5, 30, tzinfo=UTC), 40, _rows_at),
        _daily_rows(date(2022, 1, 1), today),
    )

    _run(graph, monkeypatch, datetime(2026, 6, 1, 6, tzinfo=UTC), 1000.0)

    stored = _stored(session, check)
    days = [date.fromisoformat(p["day"]) for p in stored["daily"]["points"]]
    assert days[0] == today - timedelta(days=monitors.PERIOD_HISTORY_YEARS * 366 + 14)
    assert len(stored["observations"]) == 14  # the run-by-run points keep their window


def test_a_dry_run_records_no_day(
    graph: tuple[Any, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, conn, check = graph
    monkeypatch.setattr(anomaly, "measure_metric", lambda *a, **k: 1000.0)
    outcome = anomaly.build_anomaly_executor(
        session,
        connection=conn,
        target_table="orders",
        target_schema="shop",
        target_catalog=None,
        secret_store=FakeSecretStore(default="secret", raise_on_write=True),
        persist=False,
        clock=lambda: datetime(2026, 3, 1, tzinfo=UTC),
    )(check)

    assert outcome.observed_value is not None and outcome.observed_value["dry_run"] is True
    assert get_baseline(session, check.id) is None
