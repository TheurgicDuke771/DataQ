"""A per-period check's history: seeded once from the table, then accumulated (ADR 0048)."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine.default import DefaultDialect

from backend.app.datasources import monitors
from backend.app.datasources.monitors import MonitorConfigError
from backend.app.db.models import Check, Connection, Suite, User
from backend.app.services import anomaly, monitor_baseline
from backend.app.services.monitor_baseline import get_baseline
from backend.tests.support.fake_secret_store import FakeSecretStore

_COUNT = {"target_metric": "period_row_count", "date_column": "ordered_at"}
_SUM = {"target_metric": "period_sum", "date_column": "ordered_at", "value_column": "amount"}


def _at(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=UTC)


# ── config and retention ─────────────────────────────────────────────────────


def test_seeding_is_on_unless_switched_off() -> None:
    on = monitors.anomaly_params(_COUNT).period
    off = monitors.anomaly_params({**_COUNT, "backfill": False}).period

    assert on is not None and off is not None
    assert (on.backfill, off.backfill) == (True, False)


@pytest.mark.parametrize("value", ["no", 0, 1, "false"])
def test_a_backfill_switch_that_is_not_a_boolean_is_refused(value: Any) -> None:
    with pytest.raises(MonitorConfigError, match="backfill must be true or false"):
        monitors.anomaly_params({**_COUNT, "backfill": value})


def test_the_backfill_switch_is_refused_on_a_whole_table_metric() -> None:
    with pytest.raises(MonitorConfigError, match="anomaly backfill applies only to"):
        monitors.validate_monitor_config(
            "anomaly", {"target_metric": "row_count", "backfill": False}
        )


@pytest.mark.parametrize(
    ("config", "kept"),
    [
        ({**_COUNT, "window": 14}, 14 + 3 * 366),
        ({**_COUNT, "window": 14, "seasonality": True}, 14 * 7 + 3 * 366),
        ({**_COUNT, "window": 8, "period": "week"}, 8 + 3 * 53),
        # Every other anomaly check keeps what it kept before.
        ({"target_metric": "row_count", "window": 14}, 14),
        ({"target_metric": "row_count", "window": 14, "seasonality": True}, 98),
    ],
)
def test_a_per_period_check_keeps_three_years_on_top_of_its_window(
    config: dict[str, Any], kept: int
) -> None:
    assert monitors.anomaly_params(config).retained_observations == kept


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (date(2026, 10, 12), date(2026, 10, 12)),
        (datetime(2026, 10, 12, 0, 0), date(2026, 10, 12)),
        (datetime(2026, 10, 12, 0, 0, tzinfo=UTC), date(2026, 10, 12)),
        ("2026-10-12", date(2026, 10, 12)),
        ("2026-10-12 00:00:00", date(2026, 10, 12)),
    ],
)
def test_a_history_rows_day_accepts_what_drivers_return(value: Any, expected: date) -> None:
    assert monitors.history_day(value) == expected


@pytest.mark.parametrize("value", [None, 20261012, "last tuesday", b"2026-10-12"])
def test_a_history_row_with_no_readable_day_is_an_error_that_names_only_its_type(
    value: Any,
) -> None:
    with pytest.raises(MonitorConfigError, match="is not a date") as exc_info:
        monitors.history_day(value)

    assert "tuesday" not in str(exc_info.value)


def test_the_history_statement_gives_one_row_per_day_on_a_real_database(
    db_session: Any,
) -> None:
    db_session.execute(
        text(
            "CREATE TEMP TABLE period_history (ordered_at timestamp, amount numeric(10, 2)) "
            "ON COMMIT DROP"
        )
    )
    db_session.execute(
        text(
            "INSERT INTO period_history VALUES "
            "('2026-10-08 23:59:59', 999), "  # before the range
            "('2026-10-09 00:00:00', 10.50), ('2026-10-09 18:00:00', 20.25), "
            "('2026-10-11 09:00:00', NULL), ('2026-10-11 10:00:00', 5), "
            "('2026-10-12 00:00:00', 999)"  # the end is exclusive
        )
    )
    target = monitors.qualified_table(table="period_history", schema=None, catalog=None)

    def rows(config: dict[str, Any]) -> list[tuple[Any, Any]]:
        spec = monitors.anomaly_params(config).period
        assert spec is not None
        statement = monitors.period_history_statement(
            target, spec, start=date(2026, 10, 9), end=date(2026, 10, 12)
        )
        return sorted(tuple(r) for r in db_session.execute(statement).all())

    assert rows(_COUNT) == [(date(2026, 10, 9), 2), (date(2026, 10, 11), 2)]
    assert rows(_SUM) == [
        (date(2026, 10, 9), Decimal("30.75")),
        (date(2026, 10, 11), Decimal("5.00")),
    ]


# ── seeding ──────────────────────────────────────────────────────────────────


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


class _Warehouse:
    """Stands in for the live connection: answers the history query with ``history`` and the
    period query with ``value``, and records which of the two it was asked."""

    def __init__(self, history: Any = (), value: Any = 100) -> None:
        self.history = history
        self.value = value
        self.asked: list[str] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> _Warehouse:
        warehouse = self

        class _Res:
            def __init__(self, kind: str) -> None:
                self.kind = kind

            def scalar(self) -> Any:
                return warehouse.value

            def all(self) -> Any:
                if isinstance(warehouse.history, Exception):
                    raise warehouse.history
                return list(warehouse.history)

        class _Conn:
            dialect = DefaultDialect()

            def execute(self, statement: Any) -> Any:
                kind = "history" if "history_start" in statement.compile().params else "period"
                warehouse.asked.append(kind)
                return _Res(kind)

        @contextmanager
        def fake_open(connection: Connection, secret_store: Any) -> Any:
            yield _Conn()

        monkeypatch.setattr(anomaly, "_open_connection", fake_open)
        return self


def _seed(
    monkeypatch: pytest.MonkeyPatch, history: Any, config: dict[str, Any], before: date
) -> list[tuple[str, float, bool]]:
    _Warehouse(history).install(monkeypatch)
    points = anomaly.seed_history(
        _connection(),
        table="orders",
        schema="shop",
        catalog=None,
        params=monitors.anomaly_params(config),
        secret_store=FakeSecretStore(default="secret", raise_on_write=True),
        before=before,
    )
    return [(p.ts.date().isoformat(), p.value, p.observed) for p in points]


def test_seeding_starts_after_the_first_period_with_rows_and_counts_later_gaps_as_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = [(date(2026, 10, 8), 40), (date(2026, 10, 9), Decimal("55")), ("2026-10-11", "30")]

    points = _seed(monkeypatch, history, _COUNT, date(2026, 10, 13))

    # The 8th is when the table began, so it may be a part-day and is left out; nothing
    # before it is invented; the 10th and the 12th had no rows, so zero.
    assert points == [
        ("2026-10-09", 55.0, False),
        ("2026-10-10", 0.0, False),
        ("2026-10-11", 30.0, False),
        ("2026-10-12", 0.0, False),
    ]


def test_seeding_adds_days_up_into_weeks(monkeypatch: pytest.MonkeyPatch) -> None:
    history = [
        (date(2026, 9, 28), Decimal("10.50")),  # Monday
        (date(2026, 10, 4), Decimal("1.25")),  # Sunday of the same week
        (date(2026, 10, 5), None),  # a day whose amounts were all NULL
        (date(2026, 10, 7), Decimal("4")),
    ]

    points = _seed(monkeypatch, history, {**_SUM, "period": "week"}, date(2026, 10, 12))

    # The first week is the one the table began in: it is not kept as a full week.
    assert points == [("2026-10-05", 4.0, False)]


def test_a_table_that_began_midweek_does_not_seed_a_short_first_week(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = [(date(2026, 9, 24), 400)] + [  # a Thursday, then full weeks at 100 a day
        (date(2026, 9, 28) + timedelta(days=i), 100) for i in range(14)
    ]

    points = _seed(monkeypatch, history, {**_COUNT, "period": "week"}, date(2026, 10, 12))

    assert points == [("2026-09-28", 700.0, False), ("2026-10-05", 700.0, False)]


def test_seeding_a_table_with_no_rows_in_range_gives_no_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _seed(monkeypatch, [], _COUNT, date(2026, 10, 13)) == []


def test_seeding_ignores_a_row_dated_outside_the_range_it_asked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A warehouse session in another zone can cast a boundary row to the day outside. One
    before the range would otherwise become the start of the history, with every day from
    there counted as zero."""
    history = [
        (date(2023, 10, 10), 999),
        (date(2026, 10, 13), 999),
        (date(2026, 10, 11), 3),
        (date(2026, 10, 12), 7),
    ]

    assert _seed(monkeypatch, history, _COUNT, date(2026, 10, 13)) == [("2026-10-12", 7.0, False)]


def test_seeding_reads_back_no_further_than_the_years_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warehouse = _Warehouse([]).install(monkeypatch)
    seen: dict[str, Any] = {}
    real = monitors.period_history_statement

    def spy(target: Any, spec: Any, *, start: date, end: date) -> Any:
        seen.update(start=start, end=end)
        return real(target, spec, start=start, end=end)

    monkeypatch.setattr(anomaly, "period_history_statement", spy)
    anomaly.seed_history(
        _connection(),
        table="orders",
        schema="shop",
        catalog=None,
        params=monitors.anomaly_params(_COUNT),
        secret_store=FakeSecretStore(default="secret", raise_on_write=True),
        before=date(2026, 10, 13),
    )

    # 3 x 366 days: never a day short of three years, whichever of them are leap years.
    assert seen == {"start": date(2023, 10, 11), "end": date(2026, 10, 13)}
    assert warehouse.asked == ["history"]


# ── the executor ─────────────────────────────────────────────────────────────


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


# The 4th (when the table began, so not kept) to the 11th.
_HISTORY = [(date(2026, 10, day), 100 + day) for day in range(4, 12)]


def _run(graph: tuple[Any, Connection, Check], now: str, *, persist: bool = True) -> Any:
    session, conn, check = graph
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


def _stored(graph: tuple[Any, Connection, Check]) -> list[tuple[str, float, str]]:
    session, _, check = graph
    row = get_baseline(session, check.id)
    assert row is not None
    return [
        (o["ts"][:10], o["value"], o.get("source", "observed"))
        for o in row.baseline["observations"]
    ]


def test_the_first_run_seeds_the_past_and_scores_at_once(
    graph: tuple[Any, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    warehouse = _Warehouse(_HISTORY, value=108).install(monkeypatch)

    outcome = _run(graph, "2026-10-13T12:00")

    stored = _stored(graph)
    assert warehouse.asked == ["period", "history"]
    # Everything before the judged day is what the table says today; the judged day is what
    # this run saw.
    assert [s for _, _, s in stored] == ["backfilled"] * 7 + ["observed"]
    assert stored[0] == ("2026-10-05", 105.0, "backfilled")
    assert stored[-1] == ("2026-10-12", 108.0, "observed")
    assert outcome.observed_value["seeded"] == {
        "status": "done",
        "points": 7,
        "from": "2026-10-05",
        "attempts": 1,
    }
    # Scored on its first run, against the last five seeded days.
    assert outcome.skipped is False
    assert outcome.observed_value["history"] == {
        "backfilled": 5,
        "observed": 0,
        "seeding": "done",
    }


def test_a_later_run_does_not_seed_again_and_the_marks_never_change(
    graph: tuple[Any, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    warehouse = _Warehouse(_HISTORY, value=108).install(monkeypatch)
    _run(graph, "2026-10-13T12:00")
    warehouse.asked.clear()

    outcome = _run(graph, "2026-10-14T12:00")

    assert warehouse.asked == ["period"]
    assert "seeded" not in outcome.observed_value
    assert outcome.observed_value["history"] == {
        "backfilled": 4,
        "observed": 1,
        "seeding": "done",
    }
    assert [s for _, _, s in _stored(graph)] == ["backfilled"] * 7 + ["observed"] * 2


def test_with_seeding_off_the_check_starts_empty_and_asks_for_no_history(
    graph: tuple[Any, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, _, check = graph
    check.config = {**check.config, "backfill": False}
    session.flush()
    warehouse = _Warehouse(_HISTORY, value=108).install(monkeypatch)

    outcome = _run(graph, "2026-10-13T12:00")

    assert warehouse.asked == ["period"]
    assert outcome.observed_value["history"]["seeding"] == "off"
    assert "seeded" not in outcome.observed_value
    assert outcome.skipped is True
    assert _stored(graph) == [("2026-10-12", 108.0, "observed")]


def test_switching_seeding_on_later_seeds_around_the_points_already_observed(
    graph: tuple[Any, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, _, check = graph
    check.config = {**check.config, "backfill": False}
    session.flush()
    warehouse = _Warehouse(_HISTORY, value=108).install(monkeypatch)
    _run(graph, "2026-10-12T12:00")  # judges the 11th, stored as observed

    check.config = {**check.config, "backfill": True}
    session.flush()
    outcome = _run(graph, "2026-10-13T12:00")

    assert outcome.observed_value["seeded"]["status"] == "done"
    stored = _stored(graph)
    # The 11th keeps what a run saw (108), not what the table says today (111).
    assert stored[-2:] == [("2026-10-11", 108.0, "observed"), ("2026-10-12", 108.0, "observed")]
    assert [s for _, _, s in stored[:-2]] == ["backfilled"] * 6
    assert warehouse.asked.count("history") == 1


def test_a_seeding_query_that_fails_does_not_fail_the_check(
    graph: tuple[Any, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    _Warehouse(RuntimeError("relation shop.orders: permission denied"), value=108).install(
        monkeypatch
    )

    outcome = _run(graph, "2026-10-13T12:00")

    assert outcome.errored is False and outcome.skipped is True
    seeded = outcome.observed_value["seeded"]
    assert (seeded["status"], seeded["attempts"], seeded["will_retry"]) == ("failed", 1, True)
    # The driver's message is not the reason shown.
    assert "permission denied" not in str(seeded)
    assert _stored(graph) == [("2026-10-12", 108.0, "observed")]


def test_a_failed_seeding_is_tried_again_and_fills_in_behind_the_observed_points(
    graph: tuple[Any, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    warehouse = _Warehouse(TimeoutError("statement timeout"), value=108).install(monkeypatch)
    _run(graph, "2026-10-13T12:00")

    warehouse.history = _HISTORY
    outcome = _run(graph, "2026-10-14T12:00")

    # Seven points kept: the scan also returned the 12th, which a run had already judged.
    assert outcome.observed_value["seeded"] == {
        "status": "done",
        "points": 7,
        "from": "2026-10-05",
        "attempts": 2,
    }
    stored = _stored(graph)
    assert [s for _, _, s in stored] == ["backfilled"] * 7 + ["observed"] * 2
    # The 12th was judged by the first run: the table's later answer for it is not used.
    assert stored[-2] == ("2026-10-12", 108.0, "observed")
    assert outcome.skipped is False


def test_seeding_gives_up_after_three_failures_and_every_result_still_says_so(
    graph: tuple[Any, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    warehouse = _Warehouse(TimeoutError("statement timeout"), value=108).install(monkeypatch)
    outcomes = [_run(graph, f"2026-10-{day}T12:00") for day in (13, 14, 15, 16, 17)]

    assert warehouse.asked.count("history") == 3
    assert [o.observed_value.get("seeded", {}).get("will_retry") for o in outcomes] == [
        True,
        True,
        False,
        None,
        None,
    ]
    # Long after the attempts stopped, a result still says why the history is short.
    assert outcomes[-1].observed_value["history"]["seeding"] == "failed"


def test_the_table_scan_happens_before_the_baseline_row_is_locked(
    graph: tuple[Any, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concurrent run of the same check waits on that lock with a short timeout; a scan of
    three years must not be inside it."""
    events: list[str] = []
    warehouse = _Warehouse(_HISTORY, value=108).install(monkeypatch)
    real_seed, real_get = anomaly.seed_history, get_baseline

    def seed(*args: Any, **kwargs: Any) -> Any:
        events.append("scan")
        return real_seed(*args, **kwargs)

    def get(session: Any, check_id: Any, *, for_update: bool = False) -> Any:
        if for_update:
            events.append("lock")
        return real_get(session, check_id, for_update=for_update)

    monkeypatch.setattr(anomaly, "seed_history", seed)
    monkeypatch.setattr(anomaly, "get_baseline", get)
    _run(graph, "2026-10-13T12:00")

    assert events == ["scan", "lock"]
    assert warehouse.asked == ["period", "history"]


def test_a_scan_that_lost_the_race_to_another_run_is_thrown_away(
    graph: tuple[Any, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    _Warehouse(_HISTORY, value=108).install(monkeypatch)
    _run(graph, "2026-10-13T12:00")
    before = _stored(graph)
    # This run's unlocked look sees nothing seeded; by the time it holds the lock, the
    # other run's seeding is there.
    real = anomaly.stored_seed
    looks: list[int] = []

    def stale_first_look(row: Any, params: Any) -> Any:
        looks.append(1)
        return None if len(looks) == 1 else real(row, params)

    monkeypatch.setattr(anomaly, "stored_seed", stale_first_look)
    outcome = _run(graph, "2026-10-13T18:00")

    assert "seeded" not in outcome.observed_value
    assert _stored(graph) == before


def test_a_dry_run_never_seeds(
    graph: tuple[Any, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    warehouse = _Warehouse(_HISTORY, value=108).install(monkeypatch)

    outcome = _run(graph, "2026-10-13T12:00", persist=False)

    session, _, check = graph
    assert warehouse.asked == ["period"]
    assert "seeded" not in outcome.observed_value
    assert outcome.observed_value["history"]["seeding"] is None
    assert get_baseline(session, check.id) is None


def test_dropping_the_baseline_seeds_it_again(
    graph: tuple[Any, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, _, check = graph
    warehouse = _Warehouse(_HISTORY, value=108).install(monkeypatch)
    _run(graph, "2026-10-13T12:00")
    _run(graph, "2026-10-14T12:00")

    monitor_baseline.rebaseline(session, check)
    session.flush()
    warehouse.asked.clear()
    outcome = _run(graph, "2026-10-15T12:00")

    assert warehouse.asked == ["period", "history"]
    assert outcome.observed_value["seeded"]["status"] == "done"
    assert _stored(graph)[-1] == ("2026-10-14", 108.0, "observed")


def test_changing_what_the_check_buckets_by_seeds_the_new_history(
    graph: tuple[Any, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, _, check = graph
    warehouse = _Warehouse(_HISTORY, value=108).install(monkeypatch)
    _run(graph, "2026-10-13T12:00")

    check.config = {**check.config, "date_column": "shipped_at"}
    session.flush()
    warehouse.history = [(date(2026, 10, 10), 7), (date(2026, 10, 11), 9)]
    warehouse.asked.clear()
    _run(graph, "2026-10-13T18:00")

    assert warehouse.asked == ["period", "history"]
    assert _stored(graph) == [
        ("2026-10-11", 9.0, "backfilled"),
        ("2026-10-12", 108.0, "observed"),
    ]


def test_three_years_of_seeded_days_survive_the_trim(
    graph: tuple[Any, Connection, Check], monkeypatch: pytest.MonkeyPatch
) -> None:
    start = date(2023, 10, 12)
    history = [(start + timedelta(days=i), 100) for i in range((date(2026, 10, 12) - start).days)]
    _Warehouse(history, value=100).install(monkeypatch)

    _run(graph, "2026-10-13T12:00")

    stored = _stored(graph)
    assert (stored[0][0], stored[-1][0], len(stored)) == ("2023-10-13", "2026-10-12", 1096)
