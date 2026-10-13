"""Per-period anomaly metrics: rows or a sum per day or week, one point per period (ADR 0048)."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine.default import DefaultDialect

from backend.app.datasources import monitors
from backend.app.datasources.monitors import MonitorConfigError, PeriodSpec, monitor_outcome
from backend.app.db.models import Check, Connection, Suite, User
from backend.app.services import anomaly
from backend.app.services.monitor_baseline import get_baseline
from backend.tests.support.fake_secret_store import FakeSecretStore

_COUNT = {"target_metric": "period_row_count", "date_column": "ordered_at"}
_SUM = {"target_metric": "period_sum", "date_column": "ordered_at", "value_column": "amount"}


def _spec(**overrides: Any) -> PeriodSpec:
    params = monitors.anomaly_params({**_COUNT, **overrides})
    assert params.period is not None
    return params.period


# ── config ───────────────────────────────────────────────────────────────────


def test_defaults_are_a_utc_day_settled_for_six_hours() -> None:
    spec = _spec()

    assert (spec.unit, spec.timezone, spec.settle_hours, spec.value_column) == (
        "day",
        "UTC",
        6,
        None,
    )


def test_the_sum_metric_keeps_its_value_column_with_its_case() -> None:
    params = monitors.anomaly_params({**_SUM, "value_column": "NetAmount", "period": "week"})

    assert params.period == PeriodSpec("ordered_at", "week", "NetAmount", "UTC", 6)


@pytest.mark.parametrize(
    "config",
    [
        {"target_metric": "period_row_count"},  # date_column is required
        {**_COUNT, "date_column": "a; DROP TABLE x"},
        {**_COUNT, "period": "month"},
        {**_COUNT, "value_column": "amount"},  # a count sums nothing
        {"target_metric": "period_sum", "date_column": "ordered_at"},  # value_column required
        {**_SUM, "value_column": "a b"},
        {**_COUNT, "timezone": "Mars/Olympus"},
        # Spelled exactly, or a case-insensitive filesystem accepts what the worker refuses.
        {**_COUNT, "timezone": "europe/london"},
        {**_COUNT, "timezone": "utc"},
        # Resolves on Linux to whatever zone the container is in.
        {**_COUNT, "timezone": "localtime"},
        {**_COUNT, "timezone": "../../etc/passwd"},
        {**_COUNT, "timezone": 5},
        {**_COUNT, "settle_hours": -1},
        {**_COUNT, "settle_hours": 169},
        {**_COUNT, "settle_hours": True},
        {**_COUNT, "column": "loaded_at"},  # the freshness key, not this metric's
    ],
)
def test_a_malformed_period_config_is_refused(config: dict[str, Any]) -> None:
    with pytest.raises(MonitorConfigError):
        monitors.validate_monitor_config("anomaly", config)


@pytest.mark.parametrize(
    "key", ["date_column", "period", "value_column", "timezone", "settle_hours"]
)
def test_a_period_key_on_a_whole_table_metric_is_refused_when_authoring(key: str) -> None:
    """Silently ignoring it would leave the author believing the check buckets by date."""
    config = {"target_metric": "row_count", key: "day"}

    with pytest.raises(MonitorConfigError, match=f"anomaly {key} applies only to"):
        monitors.validate_monitor_config("anomaly", config)
    # A check stored before these keys existed, when unknown keys were ignored, still runs.
    assert monitors.anomaly_params(config).period is None


def test_a_key_set_to_null_is_a_key_left_out() -> None:
    nulls = {"period": None, "timezone": None, "settle_hours": None, "value_column": None}

    assert monitors.anomaly_params({**_COUNT, **nulls}).period == _spec()
    monitors.validate_monitor_config("anomaly", {"target_metric": "row_count", **nulls})


def test_utc_needs_no_time_zone_database(monkeypatch: pytest.MonkeyPatch) -> None:
    import zoneinfo

    def missing(name: str) -> Any:
        raise KeyError(name)

    monkeypatch.setattr(zoneinfo, "available_timezones", set)
    monkeypatch.setattr(zoneinfo, "ZoneInfo", missing)

    assert _spec().settled(_at("2026-10-13T06:00")) == (date(2026, 10, 12), date(2026, 10, 13))


# ── which period is judged ───────────────────────────────────────────────────


def _at(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=UTC)


@pytest.mark.parametrize(
    ("now", "overrides", "expected"),
    [
        # Tuesday 13 Oct 2026. Yesterday closed at 00:00 and settles at 06:00.
        ("2026-10-13T05:59", {}, (date(2026, 10, 11), date(2026, 10, 12))),
        ("2026-10-13T06:00", {}, (date(2026, 10, 12), date(2026, 10, 13))),
        ("2026-10-13T00:00", {"settle_hours": 0}, (date(2026, 10, 12), date(2026, 10, 13))),
        # 20:00 UTC is already 01:30 on the 14th in Kolkata, where the 13th has closed.
        (
            "2026-10-13T20:00",
            {"timezone": "Asia/Kolkata", "settle_hours": 1},
            (date(2026, 10, 13), date(2026, 10, 14)),
        ),
        ("2026-10-13T20:00", {"settle_hours": 1}, (date(2026, 10, 12), date(2026, 10, 13))),
        # Weeks run Monday to Sunday; the week of 5 Oct settled on Monday the 12th at 06:00.
        ("2026-10-12T05:59", {"period": "week"}, (date(2026, 9, 28), date(2026, 10, 5))),
        ("2026-10-12T06:00", {"period": "week"}, (date(2026, 10, 5), date(2026, 10, 12))),
        ("2026-10-18T23:00", {"period": "week"}, (date(2026, 10, 5), date(2026, 10, 12))),
    ],
)
def test_the_judged_period_is_the_last_one_closed_long_enough(
    now: str, overrides: dict[str, Any], expected: tuple[date, date]
) -> None:
    assert _spec(**overrides).settled(_at(now)) == expected


def test_a_daylight_saving_change_does_not_move_the_cut() -> None:
    """London fell back at 01:00 UTC on 25 Oct 2026. Six real hours after local midnight is
    05:00 UTC that day, not 06:00."""
    spec = _spec(timezone="Europe/London")

    assert spec.settled(_at("2026-10-26T05:59"))[1] == date(2026, 10, 25)
    assert spec.settled(_at("2026-10-26T06:00"))[1] == date(2026, 10, 26)
    # The day before the change, local midnight was 23:00 UTC, so it settles at 05:00 UTC.
    assert spec.settled(_at("2026-10-25T04:59"))[1] == date(2026, 10, 24)
    assert spec.settled(_at("2026-10-25T05:00"))[1] == date(2026, 10, 25)


# ── the statement ────────────────────────────────────────────────────────────


def _compiled(config: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    params = monitors.anomaly_params(config)
    assert params.period is not None
    statement = monitors.period_statement(
        monitors.qualified_table(table="orders", schema="shop", catalog=None),
        params.period,
        start=date(2026, 10, 12),
        end=date(2026, 10, 13),
    )
    compiled = statement.compile(dialect=postgresql.dialect())  # type: ignore[no-untyped-call]
    return " ".join(str(compiled).split()), dict(compiled.params)


def test_the_count_statement_selects_by_the_cast_date_inside_a_range_the_planner_can_use() -> None:
    sql, params = _compiled(_COUNT)

    assert sql == (
        "SELECT count(*) AS count_1 FROM shop.orders "
        "WHERE ordered_at >= %(period_from)s AND ordered_at < %(period_until)s "
        "AND CAST(ordered_at AS DATE) >= %(period_start)s "
        "AND CAST(ordered_at AS DATE) < %(period_end)s"
    )
    # The bare-column range is a day wider each side than the cast can move a row.
    assert params == {
        "period_from": date(2026, 10, 11),
        "period_until": date(2026, 10, 14),
        "period_start": date(2026, 10, 12),
        "period_end": date(2026, 10, 13),
    }


def test_the_sum_statement_quotes_mixed_case_columns() -> None:
    sql, _ = _compiled({**_SUM, "date_column": "OrderedAt", "value_column": "NetAmount"})

    assert 'sum("NetAmount")' in sql
    assert 'WHERE "OrderedAt" >=' in sql
    assert 'CAST("OrderedAt" AS DATE) <' in sql


def test_the_statement_gives_the_right_answer_on_a_real_database(db_session: Any) -> None:
    """Postgres only: what the query returns for a date column and for a timestamp column,
    with rows on both sides of each boundary."""
    db_session.execute(
        text(
            "CREATE TEMP TABLE period_orders (ordered_on date, ordered_at timestamp, "
            "amount numeric(10, 2)) ON COMMIT DROP"
        )
    )
    db_session.execute(
        text(
            "INSERT INTO period_orders VALUES "
            "('2026-10-11', '2026-10-11 23:59:59', 100), "
            "('2026-10-12', '2026-10-12 00:00:00', 10.50), "
            "('2026-10-12', '2026-10-12 23:59:59', 20.25), "
            "('2026-10-12', '2026-10-12 12:00:00', NULL), "
            "('2026-10-13', '2026-10-13 00:00:00', 1000)"
        )
    )
    target = monitors.qualified_table(table="period_orders", schema=None, catalog=None)

    def run(config: dict[str, Any], start: date, end: date) -> Any:
        spec = monitors.anomaly_params(config).period
        assert spec is not None
        return db_session.execute(
            monitors.period_statement(target, spec, start=start, end=end)
        ).scalar()

    day = (date(2026, 10, 12), date(2026, 10, 13))
    for column in ("ordered_on", "ordered_at"):
        count = {"target_metric": "period_row_count", "date_column": column}
        total = {"target_metric": "period_sum", "date_column": column, "value_column": "amount"}
        assert run(count, *day) == 3
        assert run(total, *day) == Decimal("30.75")
        assert run(count, date(2026, 10, 5), date(2026, 10, 12)) == 1
        # No row in the range: a count of 0, and SQL's NULL for the sum.
        assert run(count, date(2026, 1, 1), date(2026, 1, 2)) == 0
        assert run(total, date(2026, 1, 1), date(2026, 1, 2)) is None


# ── measuring ────────────────────────────────────────────────────────────────


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


def _patch_scalar(monkeypatch: pytest.MonkeyPatch, scalar: Any) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    class _Conn:
        dialect = DefaultDialect()

        def execute(self, statement: Any) -> Any:
            seen["statement"] = statement

            class _Res:
                @staticmethod
                def scalar() -> Any:
                    return scalar

            return _Res()

    @contextmanager
    def fake_open(connection: Connection, secret_store: Any) -> Any:
        yield _Conn()

    monkeypatch.setattr(anomaly, "_open_connection", fake_open)
    return seen


def _measure(config: dict[str, Any], now: str = "2026-10-13T12:00") -> float:
    return anomaly.measure_metric(
        _connection(),
        table="orders",
        schema="shop",
        catalog=None,
        params=monitors.anomaly_params(config),
        secret_store=FakeSecretStore(default="secret", raise_on_write=True),
        now=_at(now),
    )


@pytest.mark.parametrize(
    ("config", "scalar", "expected"),
    [
        (_COUNT, 412, 412.0),
        (_COUNT, Decimal("412"), 412.0),
        (_COUNT, "412", 412.0),  # a connector that hands COUNT back as text
        (_SUM, Decimal("30.75"), 30.75),
        (_SUM, 31, 31.0),
        (_SUM, 30.75, 30.75),
        (_SUM, None, 0.0),  # nothing in the period: zero was sold, not "unknown"
    ],
)
def test_measurement_accepts_what_drivers_return(
    monkeypatch: pytest.MonkeyPatch, config: dict[str, Any], scalar: Any, expected: float
) -> None:
    seen = _patch_scalar(monkeypatch, scalar)

    assert _measure(config) == expected
    params = dict(seen["statement"].compile().params)
    assert (params["period_start"], params["period_end"]) == (
        date(2026, 10, 12),
        date(2026, 10, 13),
    )


@pytest.mark.parametrize("scalar", ["30.75", True, float("nan"), b"30"])
def test_a_sum_that_is_not_a_finite_number_is_an_error_not_a_guess(
    monkeypatch: pytest.MonkeyPatch, scalar: Any
) -> None:
    _patch_scalar(monkeypatch, scalar)

    with pytest.raises(MonitorConfigError, match=r"SUM\(amount\)"):
        _measure(_SUM)


# ── the executor: one point per period ───────────────────────────────────────


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
        config={**_COUNT, "window": 5, "min_points": 3},
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
    *,
    persist: bool = True,
) -> Any:
    session, conn, check = graph
    _patch_scalar(monkeypatch, value)
    outcome = anomaly.build_anomaly_executor(
        session,
        connection=conn,
        target_table="orders",
        target_schema="shop",
        target_catalog=None,
        secret_store=FakeSecretStore(default="secret", raise_on_write=True),
        persist=persist,
        clock=lambda: _at(now),
    )(check)
    session.flush()
    return outcome


def _points(graph: tuple[Any, Connection, Check]) -> list[tuple[str, float]]:
    session, _, check = graph
    row = get_baseline(session, check.id)
    assert row is not None
    return [(o["ts"][:10], o["value"]) for o in row.baseline["observations"]]


def test_a_point_is_dated_by_its_period_and_the_result_says_which(
    graph: tuple[Any, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    outcome = _run(graph, monkeypatch, "2026-10-13T12:00", 412)

    assert _points(graph) == [("2026-10-12", 412.0)]
    assert outcome.observed_value["period"] == {
        "start": "2026-10-12",
        "end": "2026-10-13",
        "unit": "day",
        "timezone": "UTC",
        "settle_hours": 6,
    }
    assert outcome.observed_value["recorded"] is True
    assert outcome.expected_value["date_column"] == "ordered_at"
    assert outcome.expected_value["period"] == "day"


def test_a_second_run_in_the_same_period_measures_again_but_stores_nothing(
    graph: tuple[Any, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(graph, monkeypatch, "2026-10-13T07:00", 412)

    again = _run(graph, monkeypatch, "2026-10-13T19:00", 440)

    # Late rows show in this result; the stored point is what was judged first.
    assert (again.observed_value["value"], again.observed_value["recorded"]) == (440.0, False)
    assert _points(graph) == [("2026-10-12", 412.0)]


def test_a_period_is_scored_against_earlier_periods_only(
    graph: tuple[Any, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    for day, value in (("10", 100), ("11", 110), ("12", 90), ("13", 105)):
        _run(graph, monkeypatch, f"2026-10-{day}T12:00", value)

    first = _run(graph, monkeypatch, "2026-10-14T07:00", 400)
    rerun = _run(graph, monkeypatch, "2026-10-14T19:00", 400)

    assert [d for d, _ in _points(graph)] == [
        "2026-10-09",
        "2026-10-10",
        "2026-10-11",
        "2026-10-12",
        "2026-10-13",
    ]
    assert first.metric_value is not None and first.metric_value > 3
    # The re-run does not score the 13th against itself: same priors, same verdict.
    assert rerun.observed_value["points"] == first.observed_value["points"] == 4
    assert rerun.metric_value == first.metric_value


def test_an_early_run_before_the_period_settles_judges_the_one_before(
    graph: tuple[Any, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(graph, monkeypatch, "2026-10-13T12:00", 412)

    early = _run(graph, monkeypatch, "2026-10-14T01:00", 5)

    # 01:00 on the 14th: the 13th closed an hour ago and has not settled, so this is the
    # 12th again, and the half-loaded 13th is neither judged nor stored.
    assert early.observed_value["period"]["start"] == "2026-10-12"
    assert early.observed_value["recorded"] is False
    assert _points(graph) == [("2026-10-12", 412.0)]


def test_a_dry_run_stores_nothing_and_says_so(
    graph: tuple[Any, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    outcome = _run(graph, monkeypatch, "2026-10-13T12:00", 412, persist=False)

    session, _, check = graph
    assert get_baseline(session, check.id) is None
    assert (outcome.observed_value["recorded"], outcome.observed_value["dry_run"]) == (False, True)


def test_a_period_judged_out_of_turn_is_stored_in_date_order(
    graph: tuple[Any, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    """No run judged the 11th; the 12th is stored; then a longer settle delay makes the next
    run judge the 11th. Scoring and trimming both read the list as oldest first."""
    session, _, check = graph
    _run(graph, monkeypatch, "2026-10-11T12:00", 100)
    _run(graph, monkeypatch, "2026-10-13T12:00", 120)

    check.config = {**check.config, "settle_hours": 36}
    session.flush()
    # Midnight on the 14th, less 36 hours, is noon on the 12th: the 11th is the last settled day.
    _run(graph, monkeypatch, "2026-10-14T00:00", 110)

    assert _points(graph) == [("2026-10-10", 100.0), ("2026-10-11", 110.0), ("2026-10-12", 120.0)]


def test_changing_the_time_zone_or_settle_delay_keeps_the_history(
    graph: tuple[Any, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    """They decide when a period is judged, not which rows are in it."""
    session, _, check = graph
    for day in ("10", "11", "12", "13"):
        _run(graph, monkeypatch, f"2026-10-{day}T12:00", 100)

    check.config = {**check.config, "timezone": "Etc/UTC", "settle_hours": 2}
    session.flush()
    outcome = _run(graph, monkeypatch, "2026-10-14T12:00", 100)

    assert outcome.observed_value["points"] == 4
    assert len(_points(graph)) == 5


def test_points_bucketed_another_way_are_not_this_checks_history(
    graph: tuple[Any, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, _, check = graph
    for day in ("10", "11", "12", "13"):
        _run(graph, monkeypatch, f"2026-10-{day}T12:00", 100)

    check.config = {**check.config, "date_column": "shipped_at"}
    session.flush()
    outcome = _run(graph, monkeypatch, "2026-10-14T12:00", 100)

    # Orders per day by order date say nothing about orders per day by ship date.
    assert outcome.skipped is True
    assert outcome.observed_value["points"] == 0
    assert _points(graph) == [("2026-10-13", 100.0)]


def test_weekday_seasonality_goes_by_the_periods_weekday_not_the_runs(
    graph: tuple[Any, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, _, check = graph
    check.config = {**check.config, "seasonality": True, "window": 3, "min_points": 3}
    session.flush()
    # Runs on Tuesdays judge Mondays. Three Mondays at 100, then every other day at 900.
    for run_day in range(15, 30):
        monday_run = date(2026, 9, run_day).weekday() == 1
        _run(graph, monkeypatch, f"2026-09-{run_day:02d}T12:00", 100 if monday_run else 900)
    for run_day in range(1, 7):
        monday_run = date(2026, 10, run_day).weekday() == 1
        _run(graph, monkeypatch, f"2026-10-{run_day:02d}T12:00", 100 if monday_run else 900)

    outcome = _run(graph, monkeypatch, "2026-10-13T12:00", 100)  # Tuesday: judges Monday the 12th

    assert outcome.observed_value["period"]["start"] == "2026-10-12"
    assert (outcome.observed_value["mean"], outcome.metric_value) == (100.0, 0.0)


def test_a_whole_table_check_is_still_dated_by_the_run_and_has_no_period(
    graph: tuple[Any, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, _, check = graph
    check.config = {"target_metric": "row_count", "window": 5, "min_points": 3}
    session.flush()

    first = _run(graph, monkeypatch, "2026-10-13T07:00", 412)
    _run(graph, monkeypatch, "2026-10-13T19:00", 440)

    row = get_baseline(session, check.id)
    assert row is not None
    assert [o["value"] for o in row.baseline["observations"]] == [412.0, 440.0]
    assert "period" not in row.baseline
    assert "period" not in first.observed_value and "recorded" not in first.observed_value


def test_the_outcome_names_the_bucketing_for_the_sum_metric() -> None:
    outcome = monitor_outcome(
        "anomaly",
        scalar={"z_score": 1.0, "value": 30.75},
        config={**_SUM, "period": "week"},
        now=_at("2026-10-13T12:00"),
    )

    assert outcome.expected_value == {
        "monitor": "anomaly",
        "target_metric": "period_sum",
        "window": 14,
        "min_points": 7,
        "seasonality": False,
        "date_column": "ordered_at",
        "period": "week",
        "value_column": "amount",
    }
