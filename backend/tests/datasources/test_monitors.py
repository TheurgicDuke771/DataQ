"""Unit tests for the freshness/volume monitor core (no DB, pure logic)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from backend.app.datasources import monitors
from backend.app.datasources.base import MonitorSpec
from backend.app.datasources.monitors import (
    MonitorConfigError,
    build_monitor_sql,
    evaluate_monitors,
    monitor_outcome,
)

_NOW = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)


# ───────────────────────── build_monitor_sql ────────────────────────


def test_freshness_sql_selects_max_of_column() -> None:
    sql = build_monitor_sql(
        "freshness", table="ORDERS", schema="RETAIL", catalog=None, config={"column": "loaded_at"}
    )
    assert sql == "SELECT MAX(loaded_at) FROM RETAIL.ORDERS"


def test_volume_sql_counts_rows_with_catalog() -> None:
    sql = build_monitor_sql(
        "volume", table="orders", schema="sales", catalog="main", config={"min_rows": 1}
    )
    assert sql == "SELECT COUNT(*) FROM main.sales.orders"


def test_table_only_qualification() -> None:
    sql = build_monitor_sql("volume", table="ORDERS", schema=None, catalog=None, config={})
    assert sql == "SELECT COUNT(*) FROM ORDERS"


@pytest.mark.parametrize("bad", ["a; DROP TABLE x", "a-b", "1col", "a b", "", "a.b"])
def test_injection_or_bad_identifiers_are_rejected(bad: str) -> None:
    # column (freshness) and table (any) must be safe identifiers — no bind slot.
    with pytest.raises(MonitorConfigError):
        build_monitor_sql("freshness", table="T", schema=None, catalog=None, config={"column": bad})
    with pytest.raises(MonitorConfigError):
        build_monitor_sql("volume", table=bad, schema=None, catalog=None, config={})


def test_unknown_kind_raises() -> None:
    with pytest.raises(MonitorConfigError):
        build_monitor_sql("anomaly", table="T", schema=None, catalog=None, config={})


# ───────────────────────── freshness outcome ────────────────────────


def test_freshness_age_hours_is_the_metric() -> None:
    out = monitor_outcome(
        "freshness",
        scalar=_NOW - timedelta(hours=30),
        config={"column": "loaded_at"},
        now=_NOW,
    )
    assert out.success is True  # no thresholds → binary pass; thresholds band the age
    assert out.metric_value == pytest.approx(30.0)
    assert out.observed_value == {
        "max_timestamp": (_NOW - timedelta(hours=30)).isoformat(),
        "age_hours": 30.0,
    }
    assert out.errored is False


def test_freshness_future_timestamp_clamps_to_zero() -> None:
    out = monitor_outcome(
        "freshness", scalar=_NOW + timedelta(hours=5), config={"column": "ts"}, now=_NOW
    )
    assert out.metric_value == 0.0  # clock skew isn't "negatively stale"


def test_freshness_empty_table_is_operational_error() -> None:
    out = monitor_outcome("freshness", scalar=None, config={"column": "ts"}, now=_NOW)
    assert out.errored is True
    assert out.success is False
    assert out.metric_value is None
    assert "NULL" in (out.error_message or "")


def test_freshness_non_timestamp_scalar_raises() -> None:
    with pytest.raises(MonitorConfigError):
        monitor_outcome("freshness", scalar="not-a-date", config={"column": "ts"}, now=_NOW)


# ───────────────────────── volume outcome ───────────────────────────


def test_volume_in_range_passes_with_zero_deviation() -> None:
    out = monitor_outcome(
        "volume", scalar=1500, config={"min_rows": 1000, "max_rows": 2000}, now=_NOW
    )
    assert out.success is True
    assert out.metric_value == 0.0
    assert out.observed_value == {"row_count": 1500, "deviation_pct": 0.0}


def test_volume_below_floor_is_shortfall_pct() -> None:
    out = monitor_outcome(
        "volume", scalar=800, config={"min_rows": 1000, "max_rows": 2000}, now=_NOW
    )
    assert out.success is False
    assert out.metric_value == pytest.approx(20.0)  # (1000-800)/1000


def test_volume_above_ceiling_is_excess_pct() -> None:
    out = monitor_outcome(
        "volume", scalar=2500, config={"min_rows": 1000, "max_rows": 2000}, now=_NOW
    )
    assert out.success is False
    assert out.metric_value == pytest.approx(25.0)  # (2500-2000)/2000


@pytest.mark.parametrize(
    "config",
    [{"min_rows": 1000}, {"min_rows": -1, "max_rows": 5}, {"min_rows": 10, "max_rows": 5}, {}],
)
def test_volume_bad_range_raises(config: dict[str, object]) -> None:
    with pytest.raises(MonitorConfigError):
        monitor_outcome("volume", scalar=100, config=config, now=_NOW)


def test_monitor_kinds_exposed() -> None:
    assert monitors.MONITOR_KINDS == ("freshness", "volume")


# ───────────────────────── evaluate_monitors ────────────────────────


def test_evaluate_monitors_runs_each_in_order() -> None:
    # evaluate_monitors stamps its own `now`, so the freshness timestamp must be
    # relative to real now (not the fixed _NOW). A fake fetch_scalar keys off the
    # SQL: MAX(...) → a ~10h-old timestamp, COUNT → a count.
    def fetch(sql: str) -> object:
        return datetime.now(UTC) - timedelta(hours=10) if "MAX" in sql else 1500

    specs = [
        MonitorSpec(kind="freshness", config={"column": "loaded_at"}),
        MonitorSpec(kind="volume", config={"min_rows": 1000, "max_rows": 2000}),
    ]
    out = evaluate_monitors(fetch, table="ORDERS", schema="RETAIL", catalog=None, monitors=specs)

    assert [o.expectation_type for o in out] == ["monitor:freshness", "monitor:volume"]
    assert out[0].metric_value == pytest.approx(10.0, abs=0.05)  # freshness age-hours
    assert out[1].metric_value == 0.0  # volume in range


def test_evaluate_monitors_isolates_a_bad_config_monitor() -> None:
    # First monitor has an invalid range (config error); the second still runs.
    specs = [
        MonitorSpec(kind="volume", config={"min_rows": 9, "max_rows": 1}),  # max < min
        MonitorSpec(kind="volume", config={"min_rows": 1000, "max_rows": 2000}),
    ]
    out = evaluate_monitors(lambda _sql: 1500, table="T", schema=None, catalog=None, monitors=specs)
    assert out[0].errored is True
    assert out[1].errored is False and out[1].metric_value == 0.0


def test_evaluate_monitors_isolates_a_query_error() -> None:
    # A query that raises (e.g. unknown column) errors only that monitor.
    def fetch(_sql: str) -> object:
        raise RuntimeError("invalid identifier 'NOPE'")

    out = evaluate_monitors(
        fetch,
        table="T",
        schema=None,
        catalog=None,
        monitors=[MonitorSpec(kind="freshness", config={"column": "nope"})],
    )
    assert out[0].errored is True
    assert "invalid identifier" in (out[0].error_message or "")


def test_freshness_accepts_a_date_column() -> None:
    # A DATE column's MAX() is a date (not datetime) — midnight is used (the live
    # RETAIL.CUSTOMERS.SIGNUP_DATE case). _NOW=2026-06-29 12:00 → date 06-28 = 36h.
    out = monitor_outcome(
        "freshness", scalar=date(2026, 6, 28), config={"column": "signup_date"}, now=_NOW
    )
    assert out.errored is False
    assert out.metric_value == pytest.approx(36.0)


def test_freshness_naive_timestamp_assumed_utc() -> None:
    # Snowflake TIMESTAMP_NTZ returns a naive datetime; treat as UTC so the age
    # subtraction against a UTC now doesn't raise offset-naive-vs-aware.
    naive = datetime(2026, 6, 29, 2, 0, 0)  # no tzinfo
    out = monitor_outcome("freshness", scalar=naive, config={"column": "ts"}, now=_NOW)
    assert out.errored is False
    assert out.metric_value == pytest.approx(10.0)  # 12:00 - 02:00 UTC


def test_freshness_non_date_scalar_still_raises() -> None:
    with pytest.raises(MonitorConfigError):
        monitor_outcome("freshness", scalar=12345, config={"column": "ts"}, now=_NOW)
