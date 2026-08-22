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
    # Live-verified platform fact (2026-08-22): SNOWFLAKE.CORE.ROW_COUNT cannot
    # be invoked directly, in any argument spelling — volume is not in the
    # DMF matrix, and an out-of-band row must refuse cleanly.
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
    # The generic classifier read an unknown column as "connection or run
    # target looks misconfigured" (live-observed) — the DMF classifier must
    # name the actual problem.
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
