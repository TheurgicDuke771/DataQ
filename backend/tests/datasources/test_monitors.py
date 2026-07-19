"""Unit tests for the freshness/volume monitor core (no DB, pure logic)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import literal_column, select

from backend.app.datasources import monitors
from backend.app.datasources.base import CheckOutcome, MonitorSpec
from backend.app.datasources.monitors import (
    MonitorConfigError,
    build_monitor_statement,
    evaluate_monitors,
    monitor_outcome,
)

_NOW = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)


def _snowflake_sql(statement: object) -> str:
    """Render a monitor statement as Snowflake would, whitespace-normalised.

    The statement is deliberately never compiled in production (the connection's
    own dialect renders it — #476), so these assertions pick a concrete dialect to
    make the emitted SQL observable."""
    from snowflake.sqlalchemy import snowdialect

    return " ".join(str(statement.compile(dialect=snowdialect.SnowflakeDialect())).split())  # type: ignore[attr-defined]


# ─────────────────────── build_monitor_statement ────────────────────


def test_freshness_statement_selects_max_of_column() -> None:
    statement = build_monitor_statement(
        "freshness", table="orders", schema="retail", catalog=None, config={"column": "loaded_at"}
    )
    assert _snowflake_sql(statement) == "SELECT max(loaded_at) AS max_1 FROM retail.orders"


def test_volume_statement_counts_rows_with_catalog() -> None:
    statement = build_monitor_statement(
        "volume", table="orders", schema="sales", catalog="main", config={"min_rows": 1}
    )
    assert _snowflake_sql(statement) == "SELECT count(*) AS count_1 FROM main.sales.orders"


def test_table_only_qualification() -> None:
    statement = build_monitor_statement(
        "volume", table="orders", schema=None, catalog=None, config={}
    )
    assert _snowflake_sql(statement) == "SELECT count(*) AS count_1 FROM orders"


# ── #476: identifier casing ──


def test_freshness_quotes_a_mixed_case_column() -> None:
    """The #476 defect. A column created as `"Amount"` is stored mixed-case and is
    only reachable quoted; the pre-Core builder interpolated it bare, so Snowflake
    folded it to AMOUNT and the monitor failed with "invalid identifier"."""
    statement = build_monitor_statement(
        "freshness", table="orders", schema="retail", catalog=None, config={"column": "Amount"}
    )
    assert _snowflake_sql(statement) == 'SELECT max("Amount") AS max_1 FROM retail.orders'


def test_lower_case_identifiers_stay_unquoted_so_they_still_fold() -> None:
    """The compatibility half, and the reason quoting is delegated to the dialect
    rather than applied unconditionally: a lower-case name must stay BARE so the
    warehouse folds it (`order_ts` → ORDER_TS) exactly as it did before #476.
    Quoting everything would have broken every freshness monitor in existence."""
    statement = build_monitor_statement(
        "freshness", table="orders", schema="retail", catalog=None, config={"column": "order_ts"}
    )
    sql = _snowflake_sql(statement)
    assert '"' not in sql
    assert sql == "SELECT max(order_ts) AS max_1 FROM retail.orders"


def test_quoting_follows_the_dialect_not_a_hardcoded_character() -> None:
    """Unity Catalog quotes with backticks and reads `"..."` as a STRING LITERAL,
    so hand-rolled `"`-quoting would not have fixed #476 — it would have silently
    turned the column reference into a constant. Pinning both dialects keeps the
    fix from regressing into a hardcoded quote char."""
    from databricks.sqlalchemy.base import DatabricksDialect

    statement = build_monitor_statement(
        "freshness", table="orders", schema="retail", catalog=None, config={"column": "Amount"}
    )
    databricks_sql = " ".join(str(statement.compile(dialect=DatabricksDialect())).split())
    assert databricks_sql == "SELECT max(`Amount`) AS max_1 FROM retail.orders"


def test_catalog_without_schema_is_rejected() -> None:
    # A catalog with no schema would emit a 2-part `catalog.table` that Databricks
    # reads as schema.table (wrong object) — reject it as a config error up front.
    with pytest.raises(MonitorConfigError, match="catalog needs a schema"):
        build_monitor_statement("volume", table="ORDERS", schema=None, catalog="main", config={})


@pytest.mark.parametrize("bad", ["a; DROP TABLE x", "a-b", "1col", "a b", "", "a.b"])
def test_injection_or_bad_identifiers_are_rejected(bad: str) -> None:
    # column (freshness) and table (any) must be safe identifiers — no bind slot.
    with pytest.raises(MonitorConfigError):
        build_monitor_statement(
            "freshness", table="T", schema=None, catalog=None, config={"column": bad}
        )
    with pytest.raises(MonitorConfigError):
        build_monitor_statement("volume", table=bad, schema=None, catalog=None, config={})


@pytest.mark.parametrize("bad", ["a; DROP TABLE x", "a-b", "1col", "a b", "", "a.b"])
def test_bad_identifiers_never_reach_the_emitted_sql(bad: str) -> None:
    """Belt-and-braces on the widening: Core quotes, so a rejected name must be
    refused at the allowlist rather than 'made safe' by quoting — otherwise the
    catalog.schema path (deliberately emitted UNQUOTED so the dots separate parts)
    would become an interpolation hole."""
    with pytest.raises(MonitorConfigError):
        build_monitor_statement("volume", table="t", schema=bad, catalog=None, config={})
    with pytest.raises(MonitorConfigError):
        build_monitor_statement("volume", table="t", schema="s", catalog=bad, config={})


def test_unknown_kind_raises() -> None:
    with pytest.raises(MonitorConfigError):
        build_monitor_statement("anomaly", table="T", schema=None, catalog=None, config={})


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
    assert monitors.MONITOR_KINDS == ("freshness", "volume", "schema_drift")
    assert monitors.SCALAR_MONITOR_KINDS == ("freshness", "volume")
    assert monitors.STATEFUL_MONITOR_KINDS == ("schema_drift",)


# ───────────────────────── evaluate_monitors ────────────────────────


def test_evaluate_monitors_runs_each_in_order() -> None:
    # evaluate_monitors stamps its own `now`, so the freshness timestamp must be
    # relative to real now (not the fixed _NOW). A fake fetch_scalar keys off the
    # statement: max(...) → a ~10h-old timestamp, count → a count.
    def fetch(statement: Any) -> object:
        is_max = "max" in str(statement).lower()
        return datetime.now(UTC) - timedelta(hours=10) if is_max else 1500

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
    out = evaluate_monitors(
        lambda _statement: 1500, table="T", schema=None, catalog=None, monitors=specs
    )
    assert out[0].errored is True
    assert out[1].errored is False and out[1].metric_value == 0.0


def test_evaluate_monitors_isolates_a_query_error() -> None:
    # A query that raises (e.g. unknown column) errors only that monitor.
    def fetch(_statement: Any) -> object:
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


# ───────────────────── strategy registry (#726) ─────────────────────


def test_registry_addition_routes_all_three_functions(monkeypatch: pytest.MonkeyPatch) -> None:
    # The #726 AC: adding a kind = ONE registry entry — build/validate/outcome all
    # route through it with no edits to any chain (there are no chains left).
    from backend.app.datasources import monitors as m

    calls: list[str] = []
    fake = m.MonitorKindStrategy(
        kind="fake_kind",
        validate_config=lambda config: calls.append("validate"),
        outcome=lambda scalar, config, now: CheckOutcome(
            expectation_type=m.monitor_expectation_type("fake_kind"),
            success=True,
            metric_value=float(scalar),
        ),
        build_statement=lambda target, config: select(literal_column("42")).select_from(target),
    )
    monkeypatch.setitem(m.MONITOR_KIND_REGISTRY, "fake_kind", fake)

    m.validate_monitor_config("fake_kind", {})
    assert calls == ["validate"]
    statement = m.build_monitor_statement(
        "fake_kind", table="t", schema=None, catalog=None, config={}
    )
    assert _snowflake_sql(statement) == "SELECT 42 FROM t"
    outcome = m.monitor_outcome("fake_kind", scalar=7, config={}, now=datetime.now(UTC))
    assert outcome.metric_value == 7.0
    assert outcome.expectation_type == "monitor:fake_kind"


def test_registry_kind_without_sql_form_refuses_to_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A stateful kind (build_statement=None — the #592/#593 shape) must refuse the SQL
    # path with a clear config error, never build a wrong query.
    from backend.app.datasources import monitors as m

    stateful = m.MonitorKindStrategy(
        kind="stateful_kind",
        validate_config=lambda config: None,
        outcome=lambda scalar, config, now: CheckOutcome(
            expectation_type="monitor:stateful_kind", success=True
        ),
        build_statement=None,
    )
    monkeypatch.setitem(m.MONITOR_KIND_REGISTRY, "stateful_kind", stateful)
    with pytest.raises(m.MonitorConfigError, match="no scalar-SQL form"):
        m.build_monitor_statement("stateful_kind", table="t", schema=None, catalog=None, config={})


def test_monitor_kinds_derives_from_registry() -> None:
    from backend.app.datasources import monitors as m

    assert m.MONITOR_KINDS == tuple(m.MONITOR_KIND_REGISTRY)
