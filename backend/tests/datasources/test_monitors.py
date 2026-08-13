"""Unit tests for the monitor-kind core — freshness/volume statements + banding,
the shared driver-boundary helpers, and the `anomaly` config/outcome contract
(#593). No DB, no datasource: pure logic."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
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
from backend.app.services.failure_classifier import classify_failure_reason

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
    from snowflake.sqlalchemy import snowdialect

    statement = build_monitor_statement(
        "volume",
        table="orders",
        schema="sales",
        catalog="main",
        config={"min_rows": 1},
        dialect=snowdialect.SnowflakeDialect(),
    )
    assert _snowflake_sql(statement) == "SELECT count(*) AS count_1 FROM main.sales.orders"


def test_upper_case_target_still_resolves() -> None:
    """Upper-case is the DOMINANT real-world spelling — it's what the catalog
    dropdown reports for every Snowflake object — and its emitted SQL changed with
    #476 (`RETAIL.ORDERS` → `"RETAIL"."ORDERS"`). Both resolve to the same object
    (Snowflake folds unquoted to upper), but the change must be pinned rather than
    inferred."""
    statement = build_monitor_statement(
        "volume", table="ORDERS", schema="RETAIL", catalog=None, config={}
    )
    assert _snowflake_sql(statement) == 'SELECT count(*) AS count_1 FROM "RETAIL"."ORDERS"'


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


def test_a_lower_case_reserved_word_is_not_quoted_into_oblivion() -> None:
    """SQLAlchemy's Snowflake dialect reserves `copy`; **Snowflake does not**. Left
    to the compiler's defaults, a column stored COPY (created unquoted as `copy`)
    would be emitted `"copy"` and stop resolving — reintroducing the exact #476
    failure for one word, on a path that worked before the fix.

    So the quote decision is ours and depends only on case, never on the dialect's
    reserved-word set."""
    statement = build_monitor_statement(
        "freshness", table="orders", schema="retail", catalog=None, config={"column": "copy"}
    )
    assert _snowflake_sql(statement) == "SELECT max(copy) AS max_1 FROM retail.orders"


def test_three_part_names_quote_the_catalog_and_schema_per_dialect() -> None:
    """#936: a mixed-case catalog and schema now resolve correctly in the 3-part
    form, not just the table.

    Core's `schema=` slot takes one string, so the `catalog.schema` namespace is
    still pre-assembled by hand into one string — but each part now goes through
    the SAME case-based "should I quote" decision as `folding_identifier`
    (`sql._quote_namespace_part`), and a part that needs quoting is wrapped with
    the DIALECT's own `identifier_preparer.quote_identifier` rather than a
    hardcoded `"`. Was the KNOWN LIMIT pinned by this test's predecessor; inverted
    now that the fix is in."""
    from snowflake.sqlalchemy import snowdialect

    statement = build_monitor_statement(
        "volume",
        table="Orders",
        schema="Retail",
        catalog="MyCat",
        config={},
        dialect=snowdialect.SnowflakeDialect(),
    )
    assert _snowflake_sql(statement) == 'SELECT count(*) AS count_1 FROM "MyCat"."Retail"."Orders"'


def test_three_part_names_stay_bare_when_already_lower_case() -> None:
    """The compatibility half of #936: an already-lower-case catalog/schema must
    stay BARE so the warehouse still folds it, exactly like `folding_identifier`'s
    rule for a plain column — quoting everything unconditionally would break every
    existing lower-case 3-part target."""
    from snowflake.sqlalchemy import snowdialect

    statement = build_monitor_statement(
        "volume",
        table="orders",
        schema="retail",
        catalog="mycat",
        config={},
        dialect=snowdialect.SnowflakeDialect(),
    )
    sql = _snowflake_sql(statement)
    assert '"' not in sql
    assert sql == "SELECT count(*) AS count_1 FROM mycat.retail.orders"


def test_three_part_names_quote_with_databricks_backticks_too() -> None:
    """Same #936 fix, Unity Catalog's dialect: the catalog/schema quote character
    must come from THIS dialect's `identifier_preparer`, not a hardcoded `"` —
    Databricks reads `"..."` as a string literal, not an identifier."""
    from databricks.sqlalchemy.base import DatabricksDialect

    statement = build_monitor_statement(
        "volume",
        table="Orders",
        schema="Retail",
        catalog="MyCat",
        config={},
        dialect=DatabricksDialect(),
    )
    databricks_sql = " ".join(str(statement.compile(dialect=DatabricksDialect())).split())
    assert databricks_sql == "SELECT count(*) AS count_1 FROM `MyCat`.`Retail`.`Orders`"


def test_catalog_without_dialect_is_rejected() -> None:
    # A live dialect is required to quote the pre-assembled catalog.schema string
    # (#936) — a catalog with no dialect is a caller bug, not user config, so it
    # is not wrapped as MonitorConfigError like the identifier-shape checks below.
    with pytest.raises(ValueError, match="no dialect"):
        build_monitor_statement("volume", table="orders", schema="sales", catalog="main", config={})


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
    # A kind that is not in the registry at all. (`anomaly` used to stand in here,
    # but it is a REGISTERED stateful kind since #593 — it refuses for a different
    # reason, "no scalar-SQL form", which the test below pins separately.)
    with pytest.raises(MonitorConfigError, match="unknown monitor kind"):
        build_monitor_statement("not_a_kind", table="T", schema=None, catalog=None, config={})


@pytest.mark.parametrize("kind", ["schema_drift", "anomaly"])
def test_stateful_kinds_have_no_scalar_sql_form(kind: str) -> None:
    """A stateful kind is registered (so it is authorable and dispatchable) but has
    no `build_statement` — asking for one must refuse rather than fall through to a
    sibling kind's query."""
    with pytest.raises(MonitorConfigError, match="no scalar-SQL form"):
        build_monitor_statement(kind, table="T", schema=None, catalog=None, config={})


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
    assert "MAX(ts) is unavailable" in (out.error_message or "")


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
    assert monitors.MONITOR_KINDS == ("freshness", "volume", "schema_drift", "anomaly")
    assert monitors.SCALAR_MONITOR_KINDS == ("freshness", "volume")
    # The partition is DERIVED from `build_statement is None`, not hand-listed —
    # this pins that registering `anomaly` with no statement builder is the single
    # step that routed it to the stateful executor path (#593).
    assert monitors.STATEFUL_MONITOR_KINDS == ("schema_drift", "anomaly")


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
    # Classified, not raw (#900) — see the leak test below for why. The message is
    # a fixed string chosen by category, so assert it IS one of those constants
    # rather than that it contains the driver's wording.
    assert out[0].error_message == classify_failure_reason(RuntimeError("invalid identifier"))


def test_monitor_error_message_never_carries_raw_exception_text() -> None:
    """A per-monitor failure must not write the driver's message into a result row (#900).

    `error_message` flows into `results.observed_value` -> the run-detail API -> the
    UI. That sink never passes the logger-level scrubber (CLAUDE.md §10 protects
    logs, not DB columns), and Azure storage exceptions embed the full SAS-signed
    URL in their text (#828) — so a raw `str(exc)` here persists a live credential
    where any viewer of the run can read it.

    Asserted on the pipeline, not on the classifier: this is the #849 lesson —
    testing the scrub helper proves nothing about the path that forgot to call it.
    """
    secret = "sig=abcdef1234567890SECRETSIGNATURE%3D"

    def fetch(_statement: Any) -> object:
        raise RuntimeError(
            f"HttpResponseError: server failed to authenticate; "
            f"https://acct.blob.core.windows.net/c/o?se=2027-01-01&{secret}"
        )

    out = evaluate_monitors(
        fetch,
        table="T",
        schema=None,
        catalog=None,
        monitors=[MonitorSpec(kind="volume", config={"min_rows": 1})],
    )

    assert out[0].errored is True
    message = out[0].error_message or ""
    assert secret not in message
    assert "sig=" not in message
    assert "blob.core.windows.net" not in message
    # And it still says something useful rather than being blanked.
    assert message.strip()


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


# ── ISO-string timestamps (live UC finding) ──


def test_freshness_accepts_an_iso_string_scalar() -> None:
    """The Databricks SQL connector returns a TIMESTAMP column's MAX as a **str**,
    so every Unity Catalog freshness monitor errored with "is not a date/timestamp
    (got str)" — a documented-supported feature that had never once worked.

    No unit test could have caught it: the type comes from the driver, and every
    fixture here hands in a real datetime. It took running one against live UC.
    """
    ten_hours_ago = (_NOW - timedelta(hours=10)).replace(tzinfo=None)
    out = monitor_outcome(
        "freshness", scalar=ten_hours_ago.isoformat(), config={"column": "created_ts"}, now=_NOW
    )
    assert out.errored is False
    assert out.metric_value == pytest.approx(10.0)


def test_freshness_accepts_an_iso_string_with_an_offset() -> None:
    out = monitor_outcome(
        "freshness",
        scalar="2026-06-29T02:00:00+00:00",
        config={"column": "created_ts"},
        now=_NOW,  # 2026-06-29 12:00 UTC
    )
    assert out.errored is False
    assert out.metric_value == pytest.approx(10.0)


@pytest.mark.parametrize("junk", ["not-a-date", "", "n/a", "yesterday"])
def test_freshness_refuses_an_unparseable_string(junk: str) -> None:
    """`fromisoformat`, not a permissive parser: a lenient one would invent an
    instant from junk, which is the flat-file epoch trap in another costume — a
    confident wrong answer beats no answer only if it is right."""
    with pytest.raises(MonitorConfigError, match="not a parseable timestamp"):
        monitor_outcome("freshness", scalar=junk, config={"column": "ts"}, now=_NOW)


def test_the_error_names_the_source_not_a_fake_column() -> None:
    """The message used to read `freshness column 'MAX(created_ts)'` — the source
    descriptor rendered where a column name belongs. Live output, so worth fixing."""
    with pytest.raises(MonitorConfigError, match=r"freshness value from MAX\(ts\)"):
        monitor_outcome("freshness", scalar=12345, config={"column": "ts"}, now=_NOW)


# ── #989: a target cell must not ride out inside an error message ────────────


def test_an_unparseable_freshness_cell_is_not_echoed_in_the_message() -> None:
    """The message is safe-marked, so it persists VERBATIM into `results` and is
    rendered in the UI, alerts and MCP output — none of which consult the suite's
    column policy. The offending cell therefore must not be in it.

    Asserted on the persisted text, not on the exception object: the failure mode
    is a value reaching a sink, so the assertion has to be about the sink.
    """
    outcomes = monitors.run_monitor_specs(
        lambda _spec: "not-a-timestamp-at-all",
        monitors=[MonitorSpec(kind=monitors.FRESHNESS, config={"column": "signup_ts"})],
        now=datetime(2026, 7, 26, tzinfo=UTC),
    )

    (outcome,) = outcomes
    assert outcome.errored
    assert "not-a-timestamp-at-all" not in (outcome.error_message or "")
    # …but it is still reported, structurally, so the diagnostic isn't lost.
    assert outcome.observed_value == {
        "unparsed_value": "not-a-timestamp-at-all",
        "column": "signup_ts",
    }


def test_the_message_still_names_the_source_so_the_error_stays_actionable() -> None:
    """Removing the value must not turn this into "something went wrong". The user
    needs to know WHICH column, which is config and safe to state."""
    (outcome,) = monitors.run_monitor_specs(
        lambda _spec: "13/07/2026",
        monitors=[MonitorSpec(kind=monitors.FRESHNESS, config={"column": "order_ts"})],
        now=datetime(2026, 7, 26, tzinfo=UTC),
    )
    assert "order_ts" in (outcome.error_message or "")
    assert "not a parseable timestamp" in (outcome.error_message or "")


def test_a_non_string_scalar_carries_no_cell_at_all() -> None:
    """The type-mismatch branch names only the TYPE, never the value, so there is
    nothing to redact and `observed_value` stays empty. Pinned so a later edit
    doesn't quietly start echoing there instead."""
    (outcome,) = monitors.run_monitor_specs(
        lambda _spec: 12345,
        monitors=[MonitorSpec(kind=monitors.FRESHNESS, config={"column": "order_ts"})],
        now=datetime(2026, 7, 26, tzinfo=UTC),
    )
    assert outcome.errored
    assert "12345" not in (outcome.error_message or "")
    assert outcome.observed_value is None


# ───────────────────── anomaly config (#593) ─────────────────────


def _anomaly_config(**overrides: Any) -> dict[str, Any]:
    return {"target_metric": "row_count", **overrides}


def test_anomaly_defaults_are_the_documented_ones() -> None:
    params = monitors.anomaly_params(_anomaly_config())
    assert (params.window, params.min_points, params.seasonality) == (14, 7, False)
    assert params.column is None


@pytest.mark.parametrize(
    "config",
    [
        {},  # target_metric is required
        {"target_metric": "rowcount"},  # near-miss spelling
        {"target_metric": None},
        {"target_metric": "freshness_age_hours"},  # column required for this metric
        {"target_metric": "freshness_age_hours", "column": "a; DROP TABLE x"},
        {"target_metric": "row_count", "column": "loaded_at"},  # column is inapplicable
        {"target_metric": "row_count", "window": 2},  # below the floor
        {"target_metric": "row_count", "window": 91},  # above the ceiling
        {"target_metric": "row_count", "window": True},  # bool is an int subclass
        {"target_metric": "row_count", "window": 14.5},  # fractional
        {"target_metric": "row_count", "window": "14"},
        {"target_metric": "row_count", "min_points": 2},
        {"target_metric": "row_count", "window": 5, "min_points": 6},  # unreachable floor
        {"target_metric": "row_count", "seasonality": "yes"},
    ],
)
def test_anomaly_rejects_malformed_config(config: dict[str, Any]) -> None:
    with pytest.raises(MonitorConfigError):
        monitors.validate_monitor_config("anomaly", config)


def test_anomaly_accepts_an_integral_float_window() -> None:
    """A JSON client can send a whole number as 14.0; that is the same window, and
    rejecting it would be a 422 the author cannot act on."""
    assert monitors.anomaly_params(_anomaly_config(window=14.0)).window == 14


def test_anomaly_rejecting_an_inapplicable_column_names_the_reason() -> None:
    """Silently ignoring it would leave the author believing the anomaly watches
    that column when it watches COUNT(*)."""
    with pytest.raises(MonitorConfigError, match="row_count"):
        monitors.anomaly_params(_anomaly_config(column="loaded_at"))


def test_anomaly_min_points_default_never_exceeds_a_small_window() -> None:
    """window=5 with the default min_points of 7 would skip forever. The default
    clamps instead of producing a config that validates and never fires."""
    params = monitors.anomaly_params(_anomaly_config(window=5))
    assert params.min_points == 5


def test_anomaly_freshness_metric_validates_and_keeps_the_column() -> None:
    params = monitors.anomaly_params(
        {"target_metric": "freshness_age_hours", "column": "Loaded_At"}
    )
    assert params.target_metric == "freshness_age_hours"
    assert params.column == "Loaded_At"  # case preserved — the dialect quotes it


def test_anomaly_retention_is_seven_windows_when_seasonal() -> None:
    """Seasonal scoring uses only same-weekday observations, so the raw ring has to
    be seven times larger for the window to fill at all."""
    assert monitors.anomaly_params(_anomaly_config(window=14)).retained_observations == 14
    assert (
        monitors.anomaly_params(_anomaly_config(window=14, seasonality=True)).retained_observations
        == 98
    )


# ───────────────────── anomaly outcome banding (#593) ─────────────────────


def test_anomaly_outcome_metric_is_the_z_score() -> None:
    outcome = monitor_outcome(
        "anomaly",
        scalar={"z_score": 3.25, "value": 900.0, "mean": 1000.0, "stddev": 30.769},
        config=_anomaly_config(),
        now=_NOW,
    )
    assert outcome.metric_value == 3.25
    assert outcome.skipped is False
    # Like freshness: "anomalous" is defined only by a threshold, so the binary
    # fallback is pass and the thresholds do the banding (ADR 0016).
    assert outcome.success is True
    assert outcome.expected_value == {
        "monitor": "anomaly",
        "target_metric": "row_count",
        "window": 14,
        "min_points": 7,
        "seasonality": False,
    }


def test_anomaly_cold_start_is_a_skip_not_a_pass() -> None:
    """The whole point of the kind's cold-start rule: a monitor that has learned
    nothing has asserted nothing, and a synthetic pass would count as a clean
    check in the health score."""
    outcome = monitor_outcome(
        "anomaly",
        scalar={"insufficient_history": True, "points": 3, "min_points": 7},
        config=_anomaly_config(),
        now=_NOW,
    )
    assert outcome.skipped is True
    assert outcome.metric_value is None
    assert outcome.observed_value == {
        "insufficient_history": True,
        "points": 3,
        "min_points": 7,
    }


def test_anomaly_outcome_expected_value_carries_the_freshness_column() -> None:
    outcome = monitor_outcome(
        "anomaly",
        scalar={"z_score": 0.5},
        config={"target_metric": "freshness_age_hours", "column": "loaded_at"},
        now=_NOW,
    )
    assert outcome.expected_value is not None
    assert outcome.expected_value["column"] == "loaded_at"


@pytest.mark.parametrize("payload", ["not-a-dict", 42, None, {"z_score": None}, {"z_score": True}])
def test_anomaly_outcome_rejects_a_malformed_payload(payload: Any) -> None:
    """The executor is the only producer, but a payload without a numeric z must
    raise rather than band `None` (or a bool, which is an int subclass) as a metric."""
    with pytest.raises(MonitorConfigError):
        monitor_outcome("anomaly", scalar=payload, config=_anomaly_config(), now=_NOW)


# ───────────────────── shared driver-boundary helpers ─────────────────────


@pytest.mark.parametrize(
    ("scalar", "expected"),
    [(5, 5), (Decimal("32840"), 32840), ("17", 17), (5.0, 5)],
)
def test_row_count_accepts_every_driver_spelling(scalar: Any, expected: int) -> None:
    """Snowflake returns a COUNT as Decimal, Databricks as int — the shared helper
    is what keeps volume and anomaly accepting exactly the same set (#953)."""
    assert monitors.row_count_from_scalar(scalar) == expected


@pytest.mark.parametrize("scalar", [None, "abc", object()])
def test_row_count_refuses_a_non_numeric_scalar(scalar: Any) -> None:
    with pytest.raises(MonitorConfigError):
        monitors.row_count_from_scalar(scalar)


@pytest.mark.parametrize(
    "scalar",
    [
        datetime(2026, 6, 29, 6, 0, tzinfo=UTC),  # tz-aware
        datetime(2026, 6, 29, 6, 0),  # naive (TIMESTAMP_NTZ) — assumed UTC
        "2026-06-29T06:00:00",  # str (the Databricks connector, #953)
        "2026-06-29T06:00:00+00:00",
    ],
)
def test_freshness_age_hours_accepts_every_driver_spelling(scalar: Any) -> None:
    assert monitors.freshness_age_hours(scalar, now=_NOW, source="MAX(ts)") == 6.0


def test_freshness_age_hours_handles_a_plain_date() -> None:
    assert monitors.freshness_age_hours(date(2026, 6, 29), now=_NOW, source="MAX(d)") == 12.0
