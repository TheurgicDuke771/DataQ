"""`datasources.snowflake_dmf` — statement building, outcome mapping, and the
never-raise error contract (ADR 0036 §5, #895 slice 2). Pure — no warehouse;
the live half is the #953-mandated integration run (slice 2's PR records it).
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.datasources.monitors import MonitorConfigError
from backend.app.datasources.snowflake_dmf import (
    build_dmf_statement,
    evaluate_dmf_check,
    probe_dmf_capability,
)


def _stmt(**overrides: Any) -> str:
    kwargs: dict[str, Any] = {
        "kind": "expectation",
        "expectation_type": "dmf:null_count",
        "config": {"column": "order_id"},
        "table": "orders_header",
        "schema": "retail",
    }
    kwargs.update(overrides)
    return build_dmf_statement(**kwargs)


# ── statement building: the #476/#937 folding rule + the #428 allowlist ──


def test_lower_case_identifiers_stay_bare_so_the_warehouse_folds_them() -> None:
    assert _stmt() == "SELECT SNOWFLAKE.CORE.NULL_COUNT(SELECT order_id FROM retail.orders_header)"


def test_mixed_case_identifiers_are_quoted() -> None:
    stmt = _stmt(config={"column": "ORDER_TS"}, table="ORDERS_HEADER", schema="RETAIL")
    assert (
        stmt == 'SELECT SNOWFLAKE.CORE.NULL_COUNT(SELECT "ORDER_TS" FROM "RETAIL"."ORDERS_HEADER")'
    )


def test_schemaless_target_is_bare_table() -> None:
    assert "FROM orders_header)" in _stmt(schema=None)


def test_freshness_uses_the_freshness_dmf() -> None:
    stmt = _stmt(kind="freshness", expectation_type="monitor:freshness", config={"column": "ts"})
    assert stmt == "SELECT SNOWFLAKE.CORE.FRESHNESS(SELECT ts FROM retail.orders_header)"


def test_volume_is_refused_row_count_has_no_ad_hoc_form() -> None:
    # Live-verified platform fact (2026-08-22): SNOWFLAKE.CORE.ROW_COUNT cannot be invoked directly,
    # in any argument spelling — volume is not in the DMF matrix.
    with pytest.raises(MonitorConfigError):
        _stmt(kind="volume", expectation_type="monitor:volume", config={"min_rows": 1})


@pytest.mark.parametrize("bad", ["order id", 'a"b', "t;DROP TABLE x", "a.b", "", None, 42])
def test_non_identifier_column_is_refused(bad: Any) -> None:
    with pytest.raises(MonitorConfigError):
        _stmt(config={"column": bad})


def test_unmapped_kind_or_type_is_refused() -> None:
    with pytest.raises(MonitorConfigError):
        _stmt(expectation_type="expect_column_values_to_not_be_null")


# ── outcome mapping ──


def test_freshness_outcome_converts_seconds_to_age_hours() -> None:
    outcome = evaluate_dmf_check(
        lambda s: 7200,
        kind="freshness",
        expectation_type="monitor:freshness",
        config={"column": "ts"},
        table="t",
        schema=None,
    )
    assert not outcome.errored
    assert outcome.metric_value == 2.0
    assert outcome.observed_value == {"age_hours": 2.0}
    assert outcome.expectation_type == "monitor:freshness"


def test_freshness_null_scalar_is_an_error_not_a_pass() -> None:
    outcome = evaluate_dmf_check(
        lambda s: None,
        kind="freshness",
        expectation_type="monitor:freshness",
        config={"column": "ts"},
        table="t",
        schema=None,
    )
    assert outcome.errored and not outcome.success


def test_ntz_freshness_rejection_gets_the_type_guidance() -> None:
    # Live-verified: FRESHNESS refuses TIMESTAMP_NTZ, and the argument must be
    # a bare column (no CAST) — so the only honest answer is guidance.
    def boom(statement: str) -> None:
        raise RuntimeError(
            "001044 (42P13): SQL compilation error: Invalid argument types for "
            "function 'FRESHNESS$V1': (TIMESTAMP_NTZ(9))"
        )

    outcome = evaluate_dmf_check(
        boom,
        kind="freshness",
        expectation_type="monitor:freshness",
        config={"column": "ts"},
        table="t",
        schema=None,
    )
    assert outcome.errored
    assert outcome.error_message is not None
    assert "TIMESTAMP_NTZ" in outcome.error_message
    assert "GX-engine freshness monitor" in outcome.error_message


def test_unknown_column_gets_the_identifier_guidance_not_connection_blame() -> None:
    # The generic classifier read an unknown column as "connection or run target looks
    # misconfigured" (live-observed) — the DMF classifier must name the actual problem.
    def boom(statement: str) -> None:
        raise RuntimeError("000904 (42000): SQL compilation error: invalid identifier 'NOPE'")

    outcome = evaluate_dmf_check(
        boom,
        kind="expectation",
        expectation_type="dmf:null_count",
        config={"column": "nope"},
        table="t",
        schema=None,
    )
    assert outcome.errored
    assert outcome.error_message is not None
    assert "column or table does not exist" in outcome.error_message


def test_privilege_failure_gets_the_grant_remediation() -> None:
    def boom(statement: str) -> None:
        raise RuntimeError("Insufficient privileges to operate on data metric function")

    outcome = evaluate_dmf_check(
        boom,
        kind="expectation",
        expectation_type="dmf:null_count",
        config={"column": "c"},
        table="t",
        schema=None,
    )
    assert outcome.errored
    assert outcome.error_message is not None
    assert "SNOWFLAKE.DATA_METRIC_USER" in outcome.error_message


def test_column_metric_outcome_carries_the_scalar_as_metric() -> None:
    outcome = evaluate_dmf_check(
        lambda s: 7,
        kind="expectation",
        expectation_type="dmf:null_count",
        config={"column": "order_id"},
        table="t",
        schema=None,
    )
    assert outcome.success  # thresholds band it in the run service
    assert outcome.metric_value == 7.0
    assert outcome.expected_value is not None
    assert outcome.expected_value["metric"] == "NULL_COUNT"


def test_fetch_failure_is_a_classified_per_check_error_never_a_raise() -> None:
    # ADR 0036 §5: a privilege/edition failure is that check's classified error.
    # The raw driver text (which can echo the statement) must NOT survive.
    def boom(statement: str) -> Any:
        raise RuntimeError(f"SQL access control error: {statement} not authorized")

    outcome = evaluate_dmf_check(
        boom,
        kind="expectation",
        expectation_type="dmf:null_percent",
        config={"column": "order_id"},
        table="t",
        schema=None,
    )
    assert outcome.errored
    assert outcome.error_message is not None
    assert "SNOWFLAKE.CORE" not in outcome.error_message  # no raw statement echo


def test_bad_config_reaching_run_time_is_an_error_outcome() -> None:
    # Authoring refuses this, but an out-of-band row must land as a per-check
    # error, not a raise (same rule as above).
    outcome = evaluate_dmf_check(
        lambda s: 0,
        kind="comparison",
        expectation_type="anything",
        config={},
        table="t",
        schema=None,
    )
    assert outcome.errored


def test_missing_table_error_is_not_read_as_a_privilege_problem() -> None:
    # Snowflake 002003 says "does not exist or not authorized." — the tail must
    # not fall through to the grants remediation (review catch on this slice).
    def boom(statement: str) -> None:
        raise RuntimeError(
            "002003 (42S02): SQL compilation error: Object RETAIL.GONE does not "
            "exist or not authorized."
        )

    outcome = evaluate_dmf_check(
        boom,
        kind="expectation",
        expectation_type="dmf:null_count",
        config={"column": "c"},
        table="gone",
        schema="retail",
    )
    assert outcome.errored
    assert outcome.error_message is not None
    assert "does not exist" in outcome.error_message
    assert "DATA_METRIC_USER" not in outcome.error_message


def test_negative_freshness_age_clamps_to_zero_like_the_monitor_path() -> None:
    # Future-dated max / clock skew: the monitor path clamps at 0.0, so the DMF
    # path must too or the same data trends differently per engine.
    outcome = evaluate_dmf_check(
        lambda s: -1800,
        kind="freshness",
        expectation_type="monitor:freshness",
        config={"column": "ts"},
        table="t",
        schema=None,
    )
    assert not outcome.errored
    assert outcome.metric_value == 0.0


def test_connection_establishment_failure_propagates_out_of_the_runner() -> None:
    # The open-before-evaluate rule (mirrors run_monitors_over_engine): an unreachable warehouse
    # fails the RUN.
    from backend.app.datasources.snowflake import SnowflakeCheckRunner, SnowflakeConfig

    cfg = SnowflakeConfig.model_validate(
        {
            "account": "x",
            "user": "u",
            "database": "d",
            "schema": "s",
            "warehouse": "w",
            "role": "r",
        }
    )
    runner = SnowflakeCheckRunner(cfg, "secret")

    class _DeadEngine:
        def get(self) -> None:
            raise ConnectionError("warehouse unreachable")

    runner._engine = _DeadEngine()  # type: ignore[assignment]
    with pytest.raises(ConnectionError):
        runner.run_native_check(
            kind="expectation",
            expectation_type="dmf:null_count",
            config={"column": "c"},
            table="t",
            schema=None,
        )


def test_dmf_types_derive_their_dimension() -> None:
    # NULL here would render dmf-covered assets as scorecard coverage gaps
    # (#889); unlike custom SQL each metric has exactly one honest dimension.
    from backend.app.services.check_dimension import derive_dimension

    assert derive_dimension(expectation_type="dmf:null_count", kind="expectation") == "completeness"
    assert (
        derive_dimension(expectation_type="dmf:null_percent", kind="expectation") == "completeness"
    )
    assert (
        derive_dimension(expectation_type="dmf:duplicate_count", kind="expectation") == "uniqueness"
    )
    assert derive_dimension(expectation_type="dmf:unique_count", kind="expectation") == "uniqueness"
    assert (
        derive_dimension(expectation_type="dmf:blank_count", kind="expectation") == "completeness"
    )
    assert (
        derive_dimension(expectation_type="dmf:future_timestamp_percent", kind="expectation")
        == "validity"
    )


def test_a_bad_dmf_identifier_is_echoed_bounded() -> None:
    # `_quoted` shares `_ident`'s allowlist AND its bounded echo (#1787).
    from backend.app.datasources.snowflake_dmf import _quoted

    with pytest.raises(MonitorConfigError) as exc_info:
        _quoted("x" * 100_000 + ";", what="table")
    message = str(exc_info.value)
    assert message.startswith("invalid table identifier: 'xxx") and message.endswith("…")
    assert len(message) < 300


# ── connection-test-time capability probe (#1867) ───────────────────────────


class _ProbeWarehouse:
    """Answers the probe's two statement kinds: the INFORMATION_SCHEMA target lookup
    (per schema filter) and the DMF call itself."""

    def __init__(
        self,
        *,
        in_schema: tuple[str, str, str] | None = ("RETAIL", "ORDERS", "ID"),
        anywhere: tuple[str, str, str] | None = None,
        dmf_error: str | None = None,
        lookup_error: str | None = None,
    ) -> None:
        self.in_schema, self.anywhere = in_schema, anywhere
        self.dmf_error, self.lookup_error = dmf_error, lookup_error
        self.executed: list[str] = []

    def __call__(self, statement: str) -> Any:
        self.executed.append(statement)
        if "INFORMATION_SCHEMA.COLUMNS" in statement:
            if self.lookup_error:
                raise RuntimeError(self.lookup_error)
            return self.in_schema if "CURRENT_SCHEMA()" in statement else self.anywhere
        if self.dmf_error:
            raise RuntimeError(self.dmf_error)
        return (0,)


def test_probe_calls_a_system_dmf_on_a_bare_table_column_reading_zero_rows() -> None:
    # #2112: an ad-hoc DMF only accepts a bare column of a table-like object; LIMIT 0 is the
    # form that compiles (a WHERE clause is rejected as "Invalid argument types").
    wh = _ProbeWarehouse()
    assert probe_dmf_capability(wh) == {"available": True, "status": "available"}
    assert wh.executed[-1] == (
        'SELECT SNOWFLAKE.CORE.NULL_COUNT(SELECT "ID" FROM "RETAIL"."ORDERS" LIMIT 0)'
    )
    assert "CURRENT_TIMESTAMP" not in " ".join(wh.executed)


def test_probe_falls_back_to_any_schema_when_the_configured_one_has_no_table() -> None:
    wh = _ProbeWarehouse(in_schema=None, anywhere=("OTHER", "T", "C"))
    assert probe_dmf_capability(wh)["available"] is True
    assert wh.executed[-1].endswith('FROM "OTHER"."T" LIMIT 0)')


def test_probe_quotes_hostile_identifiers_from_the_catalog() -> None:
    wh = _ProbeWarehouse(in_schema=('s"x', 't"; DROP TABLE y; --', "c"))
    probe_dmf_capability(wh)
    assert wh.executed[-1] == (
        'SELECT SNOWFLAKE.CORE.NULL_COUNT(SELECT "c" FROM "s""x"."t""; DROP TABLE y; --" LIMIT 0)'
    )


def test_probe_with_nothing_to_probe_is_undetermined_not_unavailable() -> None:
    result = probe_dmf_capability(_ProbeWarehouse(in_schema=None, anywhere=None))
    assert result["available"] is None
    assert result["status"] == "undetermined"
    assert "re-checked on the next connection test" in result["reason"]


def test_probe_target_lookup_failure_is_undetermined() -> None:
    result = probe_dmf_capability(_ProbeWarehouse(lookup_error="warehouse suspended"))
    assert result["available"] is None
    assert result["status"] == "undetermined"


def test_probe_classifies_a_privilege_failure() -> None:
    result = probe_dmf_capability(
        _ProbeWarehouse(dmf_error="Insufficient privileges to operate on data metric function")
    )
    assert result["available"] is False
    assert result["status"] == "no_privilege"
    assert "SNOWFLAKE.DATA_METRIC_USER" in result["reason"]


def test_probe_classifies_an_unknown_function_as_no_privilege() -> None:
    result = probe_dmf_capability(
        _ProbeWarehouse(dmf_error="002141 (42601): Unknown function SNOWFLAKE.CORE.NULL_COUNT")
    )
    assert result["status"] == "no_privilege"


def test_probe_classifies_an_edition_failure() -> None:
    result = probe_dmf_capability(
        _ProbeWarehouse(dmf_error="Unsupported feature 'DATA METRIC FUNCTION'.")
    )
    assert result["available"] is False
    assert result["status"] == "unsupported_edition"
    assert "Enterprise Edition" in result["reason"]


# The #2112 shape itself: never again a column-type reason for a probe problem.
_NULL_COUNT_TYPE_REJECTION = (
    "001044 (42P13): SQL compilation error: error line 1 at position 7\n"
    "Invalid argument types for function 'NULL_COUNT$V1': (BOOLEAN)"
)


@pytest.mark.parametrize(
    "error",
    [
        _NULL_COUNT_TYPE_REJECTION,
        # A broken view: its "or not authorized" tail is about the view, not DMF.
        "002003 (42S02): SQL compilation error:\nObject 'R.V' does not exist or not authorized.",
        "connection failed: user=svc_dataq password=hunter2 unreachable",
    ],
)
def test_probe_failures_that_say_nothing_about_dmf_are_undetermined(error: str) -> None:
    result = probe_dmf_capability(_ProbeWarehouse(dmf_error=error))
    assert result["available"] is None
    assert result["status"] == "undetermined"
    for leaked in ("FRESHNESS", "TIMESTAMP_NTZ", "BOOLEAN", "hunter2", "svc_dataq", "R.V"):
        assert leaked not in result["reason"]


# ── #1928: BLANK_COUNT + FUTURE_TIMESTAMP_PERCENT ──────────────────────────


@pytest.mark.parametrize(
    ("expectation_type", "function"),
    [
        ("dmf:blank_count", "BLANK_COUNT"),
        ("dmf:future_timestamp_percent", "FUTURE_TIMESTAMP_PERCENT"),
    ],
)
def test_new_column_metrics_use_their_system_dmf(expectation_type: str, function: str) -> None:
    stmt = _stmt(expectation_type=expectation_type, config={"column": "Status"})
    assert stmt == f'SELECT SNOWFLAKE.CORE.{function}(SELECT "Status" FROM retail.orders_header)'


@pytest.mark.parametrize(
    "expectation_type", ["dmf:accepted_values", "dmf:schema_change_count", "dmf:freshness"]
)
def test_dmfs_without_an_ad_hoc_form_are_not_mapped(expectation_type: str) -> None:
    # ACCEPTED_VALUES and SCHEMA_CHANGE_COUNT are documented "can't call this function directly";
    # FRESHNESS is the freshness kind's evaluator, never an expectation type (ADR 0036 §4).
    with pytest.raises(MonitorConfigError):
        _stmt(expectation_type=expectation_type)


def test_future_timestamp_percent_outcome_is_the_raw_percent() -> None:
    outcome = evaluate_dmf_check(
        lambda s: 12.5,
        kind="expectation",
        expectation_type="dmf:future_timestamp_percent",
        config={"column": "order_ts"},
        table="t",
        schema=None,
    )
    assert outcome.success and not outcome.errored
    assert outcome.metric_value == 12.5
    assert outcome.observed_value == {"value": 12.5}
    assert outcome.expected_value is not None
    assert outcome.expected_value["metric"] == "FUTURE_TIMESTAMP_PERCENT"


def test_blank_count_null_scalar_is_an_error_not_a_pass() -> None:
    outcome = evaluate_dmf_check(
        lambda s: None,
        kind="expectation",
        expectation_type="dmf:blank_count",
        config={"column": "status"},
        table="t",
        schema=None,
    )
    assert outcome.errored and not outcome.success
    assert outcome.error_message == "BLANK_COUNT returned no value"


def _type_rejection(function: str, expectation_type: str, arg_type: str = "NUMBER(38,2)") -> str:
    # Live-captured shape (2026-09-27, DATAQ_READER): error line breaks before "Invalid".
    def boom(statement: str) -> None:
        raise RuntimeError(
            "001044 (42P13): SQL compilation error: error line 1 at position 7\n"
            f"Invalid argument types for function '{function}$V1': ({arg_type})"
        )

    outcome = evaluate_dmf_check(
        boom,
        kind="expectation",
        expectation_type=expectation_type,
        config={"column": "c"},
        table="t",
        schema=None,
    )
    assert outcome.errored and outcome.error_message is not None
    return outcome.error_message


def test_blank_count_type_rejection_names_the_varchar_rule() -> None:
    message = _type_rejection("BLANK_COUNT", "dmf:blank_count")
    assert "VARCHAR" in message
    assert "freshness" not in message.lower()


def test_future_timestamp_type_rejection_is_not_read_as_freshness() -> None:
    # The pre-#1928 branch keyed on FRESHNESS alone; the new temporal DMF must get its own
    # guidance, not the freshness monitor's workaround.
    message = _type_rejection(
        "FUTURE_TIMESTAMP_PERCENT", "dmf:future_timestamp_percent", "TIMESTAMP_NTZ(9)"
    )
    assert "FUTURE_TIMESTAMP_PERCENT" in message
    assert "TIMESTAMP_TZ" in message
    assert "freshness monitor" not in message


def test_an_unrecognised_type_rejection_falls_through_to_the_safe_classifier() -> None:
    message = _type_rejection("SOME_FUTURE_DMF", "dmf:null_count")
    assert "SOME_FUTURE_DMF" not in message


# ─────────────── ACCEPTED_VALUES via SYSTEM$DATA_METRIC_SCAN (#2084) ───────────────


def test_accepted_values_scan_binds_every_argument_and_escapes_the_values() -> None:
    from backend.app.datasources.snowflake_dmf import build_accepted_values_scan

    statement, params = build_accepted_values_scan(
        {"column": "STATUS", "value_set": ["cancelled", "it's", "x') OR TRUE --", 3, 2.5]},
        table="ORDERS_HEADER",
        schema="RETAIL",
    )
    assert "SYSTEM$DATA_METRIC_SCAN" in statement and "COUNT(*)" in statement
    # No value reaches the statement text itself: only the bound expression carries them.
    assert "cancelled" not in statement
    assert params["ref"] == '"RETAIL"."ORDERS_HEADER"'
    assert params["arg"] == '"STATUS"'
    assert params["expr"] == ("\"STATUS\" IN ('cancelled', 'it''s', 'x'') OR TRUE --', 3, 2.5)")


@pytest.mark.parametrize(
    "config",
    [
        {"column": "a; drop", "value_set": ["x"]},
        {"column": "STATUS", "value_set": []},
        {"column": "STATUS", "value_set": "a,b"},
        {"column": "STATUS", "value_set": [True]},
        {"column": "STATUS", "value_set": [None]},
        {"column": "STATUS", "value_set": ["x" * 1001]},
    ],
)
def test_a_bad_accepted_values_config_is_refused(config: dict[str, Any]) -> None:
    from backend.app.datasources.monitors import MonitorConfigError
    from backend.app.datasources.snowflake_dmf import build_accepted_values_scan

    with pytest.raises(MonitorConfigError):
        build_accepted_values_scan(config, table="ORDERS_HEADER", schema="RETAIL")


def test_accepted_values_evaluates_through_the_bound_scan() -> None:
    from backend.app.datasources.snowflake_dmf import evaluate_dmf_check

    seen: list[tuple[str, Any]] = []

    def fetch(statement: str, params: Any = None) -> Any:
        seen.append((statement, params))
        return 16670

    outcome = evaluate_dmf_check(
        fetch,
        kind="expectation",
        expectation_type="dmf:accepted_values",
        config={"column": "STATUS", "value_set": ["cancelled"]},
        table="ORDERS_HEADER",
        schema="RETAIL",
    )
    assert outcome.metric_value == 16670.0 and outcome.errored is False
    assert outcome.expected_value is not None
    assert outcome.expected_value["metric"] == "ACCEPTED_VALUES"
    assert seen[0][1]["expr"] == "\"STATUS\" IN ('cancelled')"


@pytest.mark.parametrize(
    ("value", "literal"),
    [
        ("x\\') OR TRUE --", "'x\\\\'') OR TRUE --'"),
        ("ends-with\\", "'ends-with\\\\'"),
        ("a\\nb", "'a\\\\nb'"),
    ],
)
def test_backslashes_are_escaped_before_quotes(value: str, literal: str) -> None:
    """Snowflake processes backslash escapes inside string literals: an undoubled backslash
    can close the literal early (live 2026-09-28: `x\\') OR TRUE --` broke the expression)."""
    from backend.app.datasources.snowflake_dmf import build_accepted_values_scan

    _statement, params = build_accepted_values_scan(
        {"column": "STATUS", "value_set": [value]}, table="T", schema=None
    )
    assert params["expr"] == f'"STATUS" IN ({literal})'
