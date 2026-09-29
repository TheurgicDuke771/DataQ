"""The `column_profile` anomaly metric (ADR 0047): every column's null % and distinct count in one
query, each baselined on its own history. Measured against real PostgreSQL (the test database),
because the cast-to-string distinct count crosses a driver boundary."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from backend.app.datasources.monitors import MonitorConfigError, anomaly_params
from backend.app.db.models import Connection
from backend.app.services import anomaly
from backend.app.services.anomaly import (
    DISTINCT,
    NULL_PCT,
    ColumnProfile,
    measure_column_profile,
    score_column_profile,
    series_key,
)
from backend.tests.support.fake_secret_store import FakeSecretStore

NOW = datetime(2026, 9, 30, 6, 0, tzinfo=UTC)
PARAMS = anomaly_params({"target_metric": "column_profile", "window": 8, "min_points": 3})


@pytest.fixture
def table(_db_engine: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    name = f"cp_{uuid.uuid4().hex[:8]}"
    with _db_engine.begin() as conn:
        conn.execute(text(f"CREATE TABLE public.{name} (id int, email text, payload jsonb)"))
        conn.execute(
            text(
                f"INSERT INTO public.{name} VALUES "
                "(1, 'a@x.io', '{\"k\": 1}'), (2, NULL, '{\"k\": 1}'), "
                "(3, 'c@x.io', '{\"k\": 2}'), (4, 'c@x.io', NULL)"
            )
        )

    @contextmanager
    def real_open(connection: Connection, secret_store: Any) -> Iterator[Any]:
        with _db_engine.connect() as conn:
            yield conn

    monkeypatch.setattr(anomaly, "_open_connection", real_open)
    yield name
    with _db_engine.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS public.{name}"))


def _pg() -> Connection:
    return Connection(
        id=uuid.uuid4(),
        name="pg",
        type="postgres",
        env="dev",
        config={"host": "h", "database": "d", "user": "u"},
        secret_ref="ref",
    )


def _measure(table: str) -> ColumnProfile:
    return measure_column_profile(
        _pg(), table=table, schema="public", catalog=None, secret_store=FakeSecretStore()
    )


def test_one_query_measures_null_rate_and_distinct_count_per_column(table: str) -> None:
    profile = _measure(table)
    assert profile.row_count == 4 and profile.distinct_available
    assert profile.series[series_key("email", NULL_PCT)] == 25.0
    assert profile.series[series_key("email", DISTINCT)] == 2.0
    # A jsonb column has no equality operator; the cast to text still counts it.
    assert profile.series[series_key("payload", DISTINCT)] == 2.0
    assert profile.series[series_key("id", NULL_PCT)] == 0.0


def test_a_failed_distinct_pass_keeps_the_null_rates(
    table: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.app.datasources.monitors import column_profile_statement as real

    def no_distinct(target: Any, columns: list[str], *, distinct: bool) -> Any:
        if distinct:
            return text("SELECT this_is_not_sql(")
        return real(target, columns, distinct=False)

    monkeypatch.setattr(anomaly, "column_profile_statement", no_distinct)
    profile = _measure(table)
    assert not profile.distinct_available
    assert profile.series == {
        series_key("id", NULL_PCT): 0.0,
        series_key("email", NULL_PCT): 25.0,
        series_key("payload", NULL_PCT): 25.0,
    }


def _history(values: list[dict[str, float]]) -> list[tuple[datetime, dict[str, float]]]:
    return [(NOW - timedelta(days=len(values) - i), v) for i, v in enumerate(values)]


def _profile(series: dict[str, float]) -> ColumnProfile:
    return ColumnProfile(
        row_count=100, series=series, columns_measured=1, columns_total=1, distinct_available=True
    )


def test_cold_start_skips_rather_than_scoring() -> None:
    payload = score_column_profile(
        _profile({"email\x1fnull_pct": 5.0}),
        _history([{"email\x1fnull_pct": 5.0}]),
        now=NOW,
        params=PARAMS,
    )
    assert payload["insufficient_history"] and "z_score" not in payload


def test_a_null_rate_jump_is_flagged_and_named() -> None:
    key = series_key("email", NULL_PCT)
    history = _history([{key: 1.0}, {key: 1.2}, {key: 0.8}, {key: 1.0}])
    payload = score_column_profile(_profile({key: 40.0}), history, now=NOW, params=PARAMS)
    assert payload["z_score"] > 30
    assert payload["deviations"][0]["column"] == "email"
    assert payload["deviations"][0]["metric"] == NULL_PCT


def test_a_never_null_column_is_not_paged_for_one_stray_null() -> None:
    """Zero spread would make any change infinitely unusual; the floor is 1 percentage point."""
    key = series_key("id", NULL_PCT)
    history = _history([{key: 0.0}] * 4)
    payload = score_column_profile(_profile({key: 0.01}), history, now=NOW, params=PARAMS)
    assert payload["z_score"] < 1


def test_distinct_growth_is_normal_but_a_collapse_is_flagged() -> None:
    key = series_key("status", DISTINCT)
    history = _history([{key: 5.0}, {key: 5.0}, {key: 5.0}, {key: 5.0}])
    grew = score_column_profile(_profile({key: 50.0}), history, now=NOW, params=PARAMS)
    collapsed = score_column_profile(_profile({key: 1.0}), history, now=NOW, params=PARAMS)
    assert grew["z_score"] == 0.0
    assert collapsed["z_score"] > 5


def test_seasonality_scores_against_the_same_weekday_only() -> None:
    key = series_key("email", NULL_PCT)
    seasonal = anomaly_params(
        {"target_metric": "column_profile", "window": 8, "min_points": 3, "seasonality": True}
    )
    weekly = [(NOW - timedelta(weeks=w), {key: 1.0}) for w in (3, 2, 1)]
    daily_noise = [(NOW - timedelta(days=d), {key: 90.0}) for d in (1, 2, 3)]
    payload = score_column_profile(
        _profile({key: 1.0}), sorted(weekly + daily_noise), now=NOW, params=seasonal
    )
    assert payload["z_score"] == 0.0


def test_column_profile_takes_no_column() -> None:
    with pytest.raises(MonitorConfigError, match="takes no column"):
        anomaly_params({"target_metric": "column_profile", "column": "email"})


def test_the_executor_learns_then_scores_a_real_table(
    table: str, db_session: Any, _db_engine: Any
) -> None:
    """Three runs build the history; then a null-rate jump in `email` is scored and banded."""
    from decimal import Decimal

    from backend.app.db.models import Check, Suite, User
    from backend.app.services.anomaly import build_anomaly_executor

    user = User(aad_object_id=uuid.uuid4().hex, email=f"u-{uuid.uuid4().hex[:8]}@x.io")
    db_session.add(user)
    db_session.flush()
    conn = _pg()
    conn.created_by = user.id
    db_session.add(conn)
    db_session.flush()
    suite = Suite(name="s", connection_id=conn.id, created_by=user.id, target={"table": table})
    db_session.add(suite)
    db_session.flush()
    check = Check(
        suite_id=suite.id,
        name="profile",
        kind="anomaly",
        expectation_type="monitor:anomaly",
        config={"target_metric": "column_profile", "window": 8, "min_points": 3},
        fail_threshold=Decimal("4"),
    )
    db_session.add(check)
    db_session.flush()
    run = build_anomaly_executor(
        db_session,
        connection=conn,
        target_table=table,
        target_schema="public",
        target_catalog=None,
        secret_store=FakeSecretStore(),
    )
    first = [run(check) for _ in range(3)]
    assert all(o.skipped for o in first)

    with _db_engine.begin() as c:
        c.execute(text(f"UPDATE public.{table} SET email = NULL"))
    outcome = run(check)

    assert not outcome.skipped and not outcome.errored
    assert outcome.metric_value is not None and outcome.metric_value >= 4
    assert outcome.observed_value is not None
    top = outcome.observed_value["deviations"][0]
    assert (top["column"], top["metric"], top["value"]) == ("email", NULL_PCT, 100.0)


def test_reserved_and_spaced_column_names_are_quoted(
    _db_engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`order` and `order id` must be quoted, and `user` must not compile to CURRENT_USER — which
    would silently report 0% nulls."""
    name = f"cp_{uuid.uuid4().hex[:8]}"
    with _db_engine.begin() as conn:
        conn.execute(
            text(
                f"CREATE TABLE public.{name} "
                '("order" int, "user" text, "order id" text, "Mixed" int)'
            )
        )
        conn.execute(
            text(
                f"INSERT INTO public.{name} VALUES (1, NULL, 'a', 1), (2, NULL, NULL, 2), "
                "(3, 'u', 'b', NULL), (4, 'u', 'b', 4)"
            )
        )

    @contextmanager
    def real_open(connection: Connection, secret_store: Any) -> Iterator[Any]:
        with _db_engine.connect() as c:
            yield c

    monkeypatch.setattr(anomaly, "_open_connection", real_open)
    try:
        profile = _measure(name)
    finally:
        with _db_engine.begin() as conn:
            conn.execute(text(f"DROP TABLE IF EXISTS public.{name}"))
    assert profile.distinct_available
    assert profile.series[series_key("user", NULL_PCT)] == 50.0
    assert profile.series[series_key("order id", NULL_PCT)] == 25.0
    assert profile.series[series_key("order", DISTINCT)] == 4.0
    assert profile.series[series_key("Mixed", NULL_PCT)] == 25.0


def test_an_unqualified_target_lists_and_counts_the_same_schema(table: str) -> None:
    profile = measure_column_profile(
        _pg(), table=table, schema=None, catalog=None, secret_store=FakeSecretStore()
    )
    assert profile.row_count == 4
    assert profile.series[series_key("email", NULL_PCT)] == 25.0
