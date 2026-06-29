"""Unit tests for the freshness/volume monitor core (no DB, pure logic)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.app.datasources import monitors
from backend.app.datasources.monitors import MonitorConfigError, build_monitor_sql, monitor_outcome

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
