"""Declared seasonal periods on the yearly anomaly score (ADR 0048 decision 5, #2440 phase 4):
calendar arithmetic, the scoring with a calendar attached, the config, and the service."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.engine.default import DefaultDialect

from backend.app.datasources import monitors
from backend.app.datasources.monitors import MonitorConfigError
from backend.app.db.models import AuditEvent, Check, Connection, SeasonalPeriod, Suite, User
from backend.app.services import anomaly, seasonal
from backend.app.services import seasonal_period_service as svc
from backend.app.services.check_service import CheckConfigInvalidError, require_seasonal_periods
from backend.app.services.seasonal import Calendar, DeclaredPeriod, History, Occurrence
from backend.tests.support.fake_secret_store import FakeSecretStore

_ID = uuid.uuid4
_DAILY = {
    "target_metric": "period_row_count",
    "date_column": "ordered_at",
    "window": 14,
    "min_points": 7,
    "yearly": True,
}


def _params(**overrides: Any) -> monitors.AnomalyParams:
    return monitors.anomaly_params({**_DAILY, **overrides})


def _recurring(name: str, start: tuple[int, int], end: tuple[int, int]) -> DeclaredPeriod:
    return DeclaredPeriod(
        id=_ID(),
        name=name,
        kind="recurring",
        start_month=start[0],
        start_day=start[1],
        end_month=end[0],
        end_day=end[1],
    )


def _dated(name: str, start: date, end: date, series: str | None = "Sale") -> DeclaredPeriod:
    return DeclaredPeriod(
        id=_ID(), name=name, kind="dated", start_date=start, end_date=end, series=series
    )


def _noisy(
    start: date, end: date, *, peaks: tuple[tuple[date, date, float], ...] = ()
) -> dict[date, float]:
    """About 1000 a day, give or take 3% (deterministic), times a factor inside each peak."""
    points: dict[date, float] = {}
    day = start
    while day < end:
        wobble = ((day.toordinal() * 37) % 61 - 30) / 1000
        value = 1000.0 * (1 + wobble)
        for first, last, factor in peaks:
            if first <= day <= last:
                value *= factor
        points[day] = value
        day += timedelta(days=1)
    return points


# ── calendar arithmetic ──────────────────────────────────────────────────────


def test_a_recurring_range_may_wrap_the_year_end_and_a_day_finds_its_occurrence() -> None:
    year_end = _recurring("Year end", (12, 28), (1, 3))

    assert year_end.occurrence(2025) == Occurrence(date(2025, 12, 28), date(2026, 1, 3))
    assert year_end.containing(date(2026, 1, 1)) == Occurrence(date(2025, 12, 28), date(2026, 1, 3))
    assert year_end.containing(date(2025, 12, 30)) == Occurrence(
        date(2025, 12, 28), date(2026, 1, 3)
    )
    assert year_end.containing(date(2026, 1, 4)) is None


def test_a_29_february_bound_is_28_february_in_a_year_without_one() -> None:
    leap = _recurring("Leap", (2, 29), (3, 2))

    assert leap.occurrence(2024) == Occurrence(date(2024, 2, 29), date(2024, 3, 2))
    assert leap.occurrence(2025) == Occurrence(date(2025, 2, 28), date(2025, 3, 2))


def test_dated_entries_of_one_series_are_earlier_occurrences_whatever_their_case() -> None:
    this_year = _dated("Sale 2026", date(2026, 11, 25), date(2026, 12, 1), series="sale")
    last_year = _dated("Sale 2025", date(2025, 11, 20), date(2025, 11, 26), series="SALE")
    other = _dated("Launch 2025", date(2025, 11, 1), date(2025, 11, 2), series="Launch")
    calendar = Calendar(entries=(this_year, last_year, other))

    this = Occurrence(date(2026, 11, 25), date(2026, 12, 1))

    earlier = calendar.earlier_occurrences(this_year, this)

    assert earlier == [Occurrence(date(2025, 11, 20), date(2025, 11, 26))]
    assert (
        calendar.earlier_occurrences(other, Occurrence(date(2025, 11, 1), date(2025, 11, 2))) == []
    )


def test_overlapping_entries_resolve_to_the_one_that_started_most_recently() -> None:
    season = _recurring("Holiday season", (11, 1), (12, 31))
    peak = _recurring("Black Friday", (11, 24), (11, 30))
    calendar = Calendar(entries=(season, peak))

    inside = calendar.inside(date(2026, 11, 26))

    assert inside is not None and inside[0].name == "Black Friday"


# ── scoring with a calendar ──────────────────────────────────────────────────

_SALE_2025 = (date(2025, 11, 20), date(2025, 11, 26))
_SALE_2026 = (date(2026, 11, 25), date(2026, 12, 1))


def _moved_sale() -> tuple[dict[date, float], Calendar]:
    """A week-long sale at five times, five days later this year than last."""
    points = _noisy(date(2024, 10, 1), date(2026, 11, 28), peaks=((*_SALE_2025, 5.0),))
    for day in (date(2026, 11, 25), date(2026, 11, 26)):
        points[day] *= 5.0
    calendar = Calendar(
        entries=(_dated("Sale 2026", *_SALE_2026), _dated("Sale 2025", *_SALE_2025))
    )
    return points, calendar


def test_a_sale_that_moved_lines_up_with_last_years_through_the_calendar_and_not_without() -> None:
    points, calendar = _moved_sale()
    friday = date(2026, 11, 27)  # the third day of this year's sale

    with_calendar = History(points, _params(), calendar).score(friday, 5000.0)
    without = History(points, _params()).score(friday, 5000.0)

    assert with_calendar.applied and with_calendar.declared is not None
    assert with_calendar.declared.entry.name == "Sale 2026"
    assert with_calendar.declared.offset_days == 2
    assert with_calendar.declared.matched == (date(2025, 11, 22),)  # the third day last year
    assert with_calendar.index == pytest.approx(5.0, rel=0.05)
    assert with_calendar.z_score is not None and with_calendar.z_score < 3
    # The nearest Friday a year back is 28 November 2025: after last year's sale ended, so
    # the weekday matching expects an ordinary day (and a loose one: the level then still
    # holds the sale days, which nothing excludes without a calendar).
    assert without.applied and without.matched[0] == date(2025, 11, 28)
    assert without.index is not None and without.index < 1.0
    assert without.z_score is not None and without.z_score > 3 > with_calendar.z_score


def test_a_sale_that_fails_to_arrive_is_flagged_inside_the_declared_period() -> None:
    points, calendar = _moved_sale()

    missing = History(points, _params(), calendar).score(date(2026, 11, 27), 1000.0)

    assert missing.applied and missing.expected == pytest.approx(5000.0, rel=0.05)
    assert missing.z_score is not None and missing.z_score > 6


def test_days_inside_a_declared_period_are_left_out_of_the_level() -> None:
    points, calendar = _moved_sale()
    points.update({d: points[d] * 5 for d in (date(2026, 11, 27),)})
    after = date(2026, 12, 3)  # back to ordinary two days after the sale
    points.update({date(2026, 12, 2): 1000.0})
    history = History(points, _params(), calendar)

    scored = history.score(after, 1000.0)
    dropped = history.score(after, 400.0)

    assert scored.applied and scored.declared is None
    assert scored.excluded_from_level > 0
    assert scored.expected == pytest.approx(1000.0, rel=0.05)
    assert scored.z_score is not None and scored.z_score < 3
    assert dropped.z_score is not None and dropped.z_score > 6


def test_an_ordinary_day_is_not_matched_to_an_earlier_years_sale_day() -> None:
    """Each earlier year's matching day fell in that year's (dated) sale, which this year
    has no entry for yet."""
    sales = tuple(
        _dated(f"Sale {year}", date(year, 11, 1), date(year, 11, 30)) for year in (2025, 2024, 2023)
    )
    points = _noisy(date(2023, 10, 1), date(2026, 11, 14), peaks=((*_SALE_2025, 5.0),))

    scored = History(points, _params(), Calendar(entries=sales)).score(date(2026, 11, 13), 1000.0)

    assert scored.as_dict() == {
        "applied": False,
        "reason": seasonal.EARLIER_YEAR_IN_DECLARED_PERIOD,
    }


def test_missing_history_is_not_blamed_on_a_declared_period() -> None:
    """One year back the matching day was in the sale; two and three years back it was an
    ordinary day the history does not hold. The blocker is the missing history."""
    sale = _recurring("Sale", (11, 20), (11, 26))
    points = _noisy(date(2025, 10, 1), date(2026, 11, 19), peaks=((*_SALE_2025, 5.0),))
    thursday = date(2026, 11, 19)

    scored = History(points, _params(), Calendar(entries=(sale,))).score(thursday, 1000.0)

    assert scored.as_dict() == {"applied": False, "reason": seasonal.NO_EARLIER_YEAR}


def test_an_earlier_occurrence_with_a_zero_level_is_not_a_first_year() -> None:
    """The shop was closed for the fortnight before last year's sale: the earlier occurrence
    exists but cannot size the period. That reason stands; the first-year rule does not
    withhold the band."""
    sale = _recurring("Sale", (11, 20), (11, 26))
    points = _noisy(date(2024, 12, 1), date(2026, 11, 22), peaks=((*_SALE_2025, 5.0),))
    for day in list(points):
        if date(2025, 10, 20) <= day < date(2025, 11, 20):
            points[day] = 0.0

    scored = History(points, _params(), Calendar(entries=(sale,))).score(date(2026, 11, 22), 5000.0)

    assert scored.as_dict() == {"applied": False, "reason": seasonal.ZERO_LEVEL}
    assert scored.declared is not None and not scored.declared.first_year
    assert scored.banded


def test_a_weekly_match_must_lie_inside_the_earlier_occurrence_on_both_sides() -> None:
    """Last year's sale began on a Thursday; this year's on a Monday. The week at offset 0
    snaps back to the Monday before last year's sale, which is not a sale week."""
    last_year = _dated("Sale 2025", date(2025, 11, 20), date(2025, 11, 30))  # a Thursday
    this_year = _dated("Sale 2026", date(2026, 11, 23), date(2026, 12, 6))  # a Monday
    points = {d: 7000.0 for d in _noisy(date(2024, 10, 7), date(2026, 11, 23)) if d.weekday() == 0}
    history = History(points, _params(period="week"), Calendar(entries=(last_year, this_year)))

    scored = history.score(date(2026, 11, 23), 7000.0)

    assert scored.applied and scored.declared is not None
    assert date(2025, 11, 17) not in scored.declared.matched
    assert scored.declared.first_year


def test_in_a_periods_first_year_a_rise_is_not_banded_and_a_drop_is_judged_as_usual() -> None:
    sale = _recurring("Sale", (11, 20), (11, 26))
    points = _noisy(date(2026, 1, 1), date(2026, 11, 22))  # less than a year: no occurrence before
    history = History(points, _params(), Calendar(entries=(sale,)))

    rise = history.score(date(2026, 11, 22), 5000.0)
    flat = history.score(date(2026, 11, 22), 990.0)
    drop = history.score(date(2026, 11, 22), 400.0)

    assert rise.applied and rise.declared is not None
    assert (rise.declared.first_year, rise.declared.banded, rise.banded) == (True, False, False)
    assert rise.index == 1.0 and rise.expected == pytest.approx(1000.0, rel=0.05)
    assert flat.declared is not None and flat.declared.banded
    assert flat.z_score is not None and flat.z_score < 3
    assert drop.declared is not None and drop.declared.banded
    assert drop.z_score is not None and drop.z_score > 6


def test_a_recurring_period_across_the_year_end_matches_the_same_offset_a_year_back() -> None:
    year_end = _recurring("Year end", (12, 28), (1, 3))
    points = _noisy(
        date(2024, 6, 1), date(2026, 1, 1), peaks=((date(2024, 12, 28), date(2025, 1, 3), 3.0),)
    )
    points.update({date(2025, 12, 28): 3000.0, date(2025, 12, 29): 3000.0})

    scored = History(points, _params(), Calendar(entries=(year_end,))).score(
        date(2026, 1, 1), 3000.0
    )

    assert scored.applied and scored.declared is not None
    assert scored.declared.matched == (date(2025, 1, 1),)
    assert scored.index == pytest.approx(3.0, rel=0.05)
    assert scored.z_score is not None and scored.z_score < 3


def test_an_offset_past_a_shorter_earlier_occurrence_has_no_match_there() -> None:
    short = _dated("Sale 2025", date(2025, 11, 20), date(2025, 11, 22))
    long = _dated("Sale 2026", date(2026, 11, 20), date(2026, 11, 26))
    points = _noisy(
        date(2024, 10, 1),
        date(2026, 11, 25),
        peaks=((date(2025, 11, 20), date(2025, 11, 22), 5.0),),
    )
    history = History(points, _params(), Calendar(entries=(short, long)))

    scored = history.score(date(2026, 11, 25), 5000.0)  # offset 5: last year's sale was 3 days

    assert scored.applied and scored.declared is not None
    assert scored.declared.first_year and not scored.declared.banded


# ── the config ───────────────────────────────────────────────────────────────


def test_seasonal_periods_parse_to_unique_ids_and_need_yearly_on_a_per_period_metric() -> None:
    a, b = _ID(), _ID()

    params = _params(seasonal_periods=[str(a), str(b), str(a)])

    assert params.seasonal_periods == (a, b)
    with pytest.raises(MonitorConfigError, match="needs yearly: true"):
        _params(yearly=False, seasonal_periods=[str(a)])
    with pytest.raises(MonitorConfigError, match="'row_count' only with yearly: true"):
        monitors.anomaly_params(
            {"target_metric": "row_count", "seasonal_periods": [str(a)]}, authoring=True
        )
    with pytest.raises(MonitorConfigError, match="applies only to target_metric"):
        monitors.anomaly_params(
            {"target_metric": "column_profile", "seasonal_periods": [str(a)]}, authoring=True
        )
    whole_table = {"target_metric": "row_count", "yearly": True, "seasonal_periods": [str(a)]}
    assert monitors.anomaly_params(whole_table, authoring=True).seasonal_periods == (a,)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("not-a-list", "must be a list"),
        (["nope"], "calendar entry ids"),
        ([42], "calendar entry ids"),
        ([str(_ID()) for _ in range(monitors.SEASONAL_PERIODS_MAX + 1)], "at most"),
    ],
)
def test_a_malformed_seasonal_periods_value_is_refused(raw: Any, message: str) -> None:
    with pytest.raises(MonitorConfigError, match=message):
        _params(seasonal_periods=raw)


# ── the service ──────────────────────────────────────────────────────────────


@pytest.fixture
def admin(db_session: Any) -> User:
    user = User(aad_object_id=_ID().hex, email=f"a-{_ID().hex[:8]}@x.io", role="admin")
    db_session.add(user)
    db_session.commit()
    return user


_BF_COLUMNS = {
    "kind": "recurring",
    "start_month": 11,
    "start_day": 24,
    "end_month": 11,
    "end_day": 30,
}
_BF = {"name": "Black Friday", **_BF_COLUMNS}


def test_create_normalises_and_audits_and_refuses_a_case_variant_name(
    db_session: Any, admin: User
) -> None:
    row = svc.create_period(db_session, {**_BF, "name": "  Black Friday "}, actor_id=admin.id)

    assert (row.name, row.kind, row.start_date, row.series) == (
        "Black Friday",
        "recurring",
        None,
        None,
    )
    event = db_session.scalars(
        select(AuditEvent).where(AuditEvent.action == "seasonal_period.create")
    ).one()
    assert event.after["name"] == "Black Friday" and event.entity_id == row.id
    with pytest.raises(svc.SeasonalPeriodNameTakenError):
        svc.create_period(db_session, {**_BF, "name": "black FRIDAY"}, actor_id=admin.id)


def test_a_dated_entry_takes_the_series_spelling_already_in_use(
    db_session: Any, admin: User
) -> None:
    first = svc.create_period(
        db_session,
        {
            "name": "Spring sale 2025",
            "kind": "dated",
            "start_date": date(2025, 4, 1),
            "end_date": date(2025, 4, 7),
            "series": "Spring sale",
        },
        actor_id=admin.id,
    )
    second = svc.create_period(
        db_session,
        {
            "name": "Spring sale 2026",
            "kind": "dated",
            "start_date": date(2026, 4, 3),
            "end_date": date(2026, 4, 12),
            "series": "SPRING SALE",
        },
        actor_id=admin.id,
    )

    assert (first.series, second.series) == ("Spring sale", "Spring sale")


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({**_BF, "name": " "}, "must not be blank"),
        ({**_BF, "name": "x" * 129}, "at most 128"),
        ({**_BF, "kind": "weekly"}, "kind must be one of"),
        ({**_BF, "start_month": 13}, "month must be between"),
        ({**_BF, "start_month": 2, "start_day": 30}, "does not exist in month"),
        ({**_BF, "end_day": True}, "whole number"),
        ({**_BF, "start_date": date(2026, 1, 1)}, "applies only to a dated entry"),
        ({**_BF, "series": "x"}, "applies only to a dated entry"),
        ({"name": "d", "kind": "dated", "start_date": date(2026, 1, 1)}, "end_date is required"),
        (
            {
                "name": "d",
                "kind": "dated",
                "start_date": date(2026, 1, 2),
                "end_date": date(2026, 1, 1),
            },
            "not be before",
        ),
        (
            {
                "name": "d",
                "kind": "dated",
                "start_date": date(2026, 1, 1),
                "end_date": date(2026, 1, 1),
                "start_month": 1,
            },
            "applies only to a recurring entry",
        ),
    ],
)
def test_an_invalid_entry_is_refused_naming_the_field(fields: dict[str, Any], message: str) -> None:
    with pytest.raises(svc.SeasonalPeriodInvalidError, match=message):
        svc.normalise(fields)


def test_29_february_is_a_valid_recurring_bound() -> None:
    assert svc.normalise({**_BF, "start_month": 2, "start_day": 29})["start_day"] == 29


def _graph(db_session: Any, owner: User, config: dict[str, Any]) -> tuple[Connection, Check]:
    conn = Connection(
        id=_ID(),
        name=f"pg-{_ID().hex[:6]}",
        type="postgres",
        env="dev",
        config={"host": "h", "database": "shop", "user": "u"},
        secret_ref="ref",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.flush()
    suite = Suite(
        name=f"s-{_ID().hex[:8]}",
        connection_id=conn.id,
        created_by=owner.id,
        target={"schema": "shop", "table": "orders"},
    )
    db_session.add(suite)
    db_session.flush()
    check = Check(
        suite_id=suite.id,
        name="daily orders",
        kind="anomaly",
        expectation_type="monitor:anomaly",
        config=config,
        fail_threshold=Decimal("3"),
    )
    db_session.add(check)
    db_session.commit()
    return conn, check


def test_deleting_an_attached_entry_is_refused_naming_the_check_and_detaching_frees_it(
    db_session: Any, admin: User
) -> None:
    row = svc.create_period(db_session, _BF, actor_id=admin.id)
    _, check = _graph(db_session, admin, {**_DAILY, "seasonal_periods": [str(row.id)]})

    with pytest.raises(svc.SeasonalPeriodInUseError) as refused:
        svc.delete_period(db_session, row.id, actor_id=admin.id)
    db_session.rollback()
    assert refused.value.detail["checks"] == [
        {
            "id": str(check.id),
            "name": "daily orders",
            "suite_id": str(check.suite_id),
            "suite_name": check.suite.name,
        }
    ]
    assert svc.attached_counts(db_session) == {row.id: 1}

    check.config = dict(_DAILY)
    db_session.commit()
    svc.delete_period(db_session, row.id, actor_id=admin.id)

    assert db_session.get(SeasonalPeriod, row.id) is None
    assert (
        db_session.scalars(select(AuditEvent).where(AuditEvent.action == "seasonal_period.delete"))
        .one()
        .before["name"]
        == "Black Friday"
    )


def test_authoring_refuses_an_unknown_entry_and_accepts_a_known_one(
    db_session: Any, admin: User
) -> None:
    row = svc.create_period(db_session, _BF, actor_id=admin.id)
    ghost = _ID()

    require_seasonal_periods(db_session, {**_DAILY, "seasonal_periods": [str(row.id)]})
    with pytest.raises(CheckConfigInvalidError) as refused:
        require_seasonal_periods(
            db_session, {**_DAILY, "seasonal_periods": [str(row.id), str(ghost)]}
        )

    assert refused.value.detail["unknown_seasonal_periods"] == [str(ghost)]
    assert str(ghost) in refused.value.message


def test_a_save_share_locks_the_attached_entries_and_a_preview_does_not(
    _db_engine: Any, db_session: Any
) -> None:
    """A delete takes FOR UPDATE on the row: it must wait behind a save that is attaching
    the entry, and must not wait behind a dry run, which saves nothing but holds its
    transaction across the live measurement."""
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Session as SASession

    pid = _ID()
    setup = SASession(bind=_db_engine)
    setup.add(SeasonalPeriod(id=pid, name=f"Lock {pid.hex[:6]}", **_BF_COLUMNS))
    setup.commit()
    probe = SASession(bind=_db_engine)
    config = {**_DAILY, "seasonal_periods": [str(pid)]}

    def delete_would_wait() -> bool:
        try:
            probe.execute(
                select(SeasonalPeriod).where(SeasonalPeriod.id == pid).with_for_update(nowait=True)
            ).all()
            return False
        except OperationalError:
            return True
        finally:
            probe.rollback()

    try:
        require_seasonal_periods(db_session, config, lock=False)
        preview_blocks = delete_would_wait()
        require_seasonal_periods(db_session, config)
        save_blocks = delete_would_wait()
    finally:
        db_session.rollback()
        probe.close()
        row = setup.get(SeasonalPeriod, pid)
        if row is not None:
            setup.delete(row)
        setup.commit()
        setup.close()

    assert (preview_blocks, save_blocks) == (False, True)


def test_the_dry_run_resolves_attached_entries_without_the_lock(monkeypatch: Any) -> None:
    from backend.app.services import dryrun_service

    seen: list[bool] = []

    def record(session: Any, config: Any, *, lock: bool = True) -> None:
        seen.append(lock)
        raise RuntimeError("stop before the measurement")

    monkeypatch.setattr(dryrun_service, "require_seasonal_periods", record)
    with pytest.raises(RuntimeError, match="stop before"):
        dryrun_service._dry_run_anomaly(
            None,  # type: ignore[arg-type]
            Connection(name="pg", type="postgres", env="dev", config={}, secret_ref="r"),
            config={**_DAILY, "seasonal_periods": [str(_ID())]},
            target={"schema": "shop", "table": "orders"},
            secret_store=FakeSecretStore(),
        )

    assert seen == [False]


def test_a_vanished_entry_is_reported_and_the_check_keeps_running(db_session: Any) -> None:
    ghost = _ID()

    calendar = svc.calendar_for(db_session, (ghost,))

    assert calendar.missing == (ghost,) and calendar.entries == ()
    scored = History({date(2026, 1, d): 100.0 for d in range(1, 20)}, _params(), calendar).score(
        date(2026, 1, 20), 100.0
    )
    assert scored.as_dict() == {"applied": False, "reason": seasonal.NO_EARLIER_YEAR}


# ── through the executor ─────────────────────────────────────────────────────


def _run(
    session: Any,
    conn: Connection,
    check: Check,
    monkeypatch: pytest.MonkeyPatch,
    now: str,
    value: float,
    history: dict[date, float],
) -> Any:
    class _Res:
        def scalar(self) -> Any:
            return value

        def all(self) -> Any:
            return list(history.items())

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
        clock=lambda: datetime.fromisoformat(now).replace(tzinfo=UTC),
    )(check)
    session.flush()
    return outcome


def test_a_run_inside_a_declared_period_reports_what_the_calendar_did_and_did_not_change(
    db_session: Any, admin: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    this_year = svc.create_period(
        db_session,
        {
            "name": "Sale 2026",
            "kind": "dated",
            "start_date": _SALE_2026[0],
            "end_date": _SALE_2026[1],
            "series": "Sale",
        },
        actor_id=admin.id,
    )
    last_year = svc.create_period(
        db_session,
        {
            "name": "Sale 2025",
            "kind": "dated",
            "start_date": _SALE_2025[0],
            "end_date": _SALE_2025[1],
            "series": "Sale",
        },
        actor_id=admin.id,
    )
    ghost = _ID()
    conn, check = _graph(
        db_session,
        admin,
        {**_DAILY, "seasonal_periods": [str(this_year.id), str(last_year.id), str(ghost)]},
    )
    table, _ = _moved_sale()

    outcome = _run(
        db_session, conn, check, monkeypatch, "2026-11-28T12:00", 5000.0, table
    )  # judges Fri the 27th

    observed = outcome.observed_value
    declared = observed["declared"]
    assert declared["attached"] == [str(this_year.id), str(last_year.id)]
    assert declared["missing"] == [str(ghost)]
    assert declared["inside"]["name"] == "Sale 2026" and declared["inside"]["offset_days"] == 2
    assert declared["inside"]["matched"] == ["2025-11-22"]
    assert (declared["inside"]["first_year"], declared["inside"]["banded"]) == (False, True)
    assert declared["z_score_without"] > 3
    assert (declared["verdict_without"], declared["verdict_changed"]) == ("fail", True)
    assert outcome.metric_value is not None and outcome.metric_value < 3
    assert outcome.expected_value["seasonal_periods"] == [
        str(this_year.id),
        str(last_year.id),
        str(ghost),
    ]


def test_a_first_year_rise_is_a_skipped_result_that_says_why(
    db_session: Any, admin: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    sale = svc.create_period(
        db_session,
        {
            "name": "Sale",
            "kind": "recurring",
            "start_month": 11,
            "start_day": 20,
            "end_month": 11,
            "end_day": 26,
        },
        actor_id=admin.id,
    )
    conn, check = _graph(db_session, admin, {**_DAILY, "seasonal_periods": [str(sale.id)]})
    table = _noisy(date(2026, 1, 1), date(2026, 11, 22))

    outcome = _run(db_session, conn, check, monkeypatch, "2026-11-23T12:00", 5000.0, table)

    observed = outcome.observed_value
    assert outcome.skipped and observed["reason"] == "within_declared_period"
    assert observed["declared"]["inside"]["first_year"] is True
    assert observed["declared"]["inside"]["banded"] is False
    assert observed["declared"]["verdict_changed"] is True
    assert observed["declared"]["verdict_without"] == "fail"
