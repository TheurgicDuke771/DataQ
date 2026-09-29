"""Custom Snowflake DMFs as checks (#2226): statement building, the identifier gate, outcome
mapping, error classification and the connection-test listing. Pure — the live Snowflake run
is recorded on the PR.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from backend.app.datasources.monitors import MonitorConfigError
from backend.app.datasources.snowflake_dmf import (
    MAX_LISTED_CUSTOM_DMFS,
    build_custom_dmf_statement,
    evaluate_dmf_check,
    list_custom_dmfs,
)
from backend.app.services.check_dimension import derive_dimension

FN = "DATAQ_DB.QUALITY.NEG_AMOUNT"


def _recording(executed: list[str], answer: Any) -> Any:
    def fetch(statement: str) -> Any:
        executed.append(statement)
        return answer

    return fetch


def _evaluate(fetch: Any, config: dict[str, Any] | None = None) -> Any:
    return evaluate_dmf_check(
        fetch,
        kind="expectation",
        expectation_type="dmf:custom",
        config=config or {"function": FN, "columns": ["AMOUNT"]},
        table="ORDERS",
        schema="RETAIL",
    )


# ── statement building ──


def test_statement_is_the_ad_hoc_form_with_every_identifier_quoted_by_the_folding_rule() -> None:
    stmt = build_custom_dmf_statement(
        {"function": FN, "columns": ["AMOUNT", "DISCOUNT"]}, table="ORDERS", schema="RETAIL"
    )
    assert stmt == (
        'SELECT "DATAQ_DB"."QUALITY"."NEG_AMOUNT"(SELECT "AMOUNT", "DISCOUNT" '
        'FROM "RETAIL"."ORDERS")'
    )


def test_lower_case_names_stay_bare_so_the_warehouse_folds_them() -> None:
    stmt = build_custom_dmf_statement(
        {"function": "db.q.neg_amount", "columns": ["amount"]}, table="orders", schema=None
    )
    assert stmt == "SELECT db.q.neg_amount(SELECT amount FROM orders)"


@pytest.mark.parametrize(
    "function",
    [
        "DB.S.F(SELECT 1); DROP TABLE T --",
        'DB.S."F"',
        "DB.S.F;",
        "S.F",
        "A.B.C.D",
        "DB..F",
        "DB.S.F G",
        "DB.S.F\x00",
        "",
        None,
        42,
        ["DB", "S", "F"],
        "D." * 400 + "F",
    ],
)
def test_a_function_name_outside_the_allowlist_is_refused(function: Any) -> None:
    with pytest.raises(MonitorConfigError):
        build_custom_dmf_statement({"function": function, "columns": ["A"]}, table="T", schema=None)


def test_a_system_dmf_is_refused_it_has_its_own_check_type() -> None:
    # Called this way ACCEPTED_VALUES returns the sentinel -1 (ADR 0036 §6).
    with pytest.raises(MonitorConfigError, match="system DMF"):
        build_custom_dmf_statement(
            {"function": "snowflake.core.accepted_values", "columns": ["A"]},
            table="T",
            schema=None,
        )


@pytest.mark.parametrize(
    "columns",
    [
        [],
        "AMOUNT",
        None,
        ["AMOUNT) FROM T; --"],
        ["AMOUNT", 1],
        [f"C{i}" for i in range(21)],
    ],
)
def test_bad_columns_are_refused(columns: Any) -> None:
    with pytest.raises(MonitorConfigError):
        build_custom_dmf_statement({"function": FN, "columns": columns}, table="T", schema=None)


def test_twenty_columns_are_accepted() -> None:
    columns = [f"C{i}" for i in range(20)]
    stmt = build_custom_dmf_statement({"function": FN, "columns": columns}, table="T", schema=None)
    assert '"C19"' in stmt


# ── outcome mapping ──


@pytest.mark.parametrize("scalar", [2, Decimal("2"), 2.0])
def test_the_scalar_is_the_metric_for_the_thresholds_to_band(scalar: Any) -> None:
    executed: list[str] = []
    outcome = _evaluate(_recording(executed, scalar))
    assert outcome.success
    assert outcome.metric_value == 2.0
    assert outcome.observed_value == {"value": 2.0}
    assert outcome.expected_value == {"engine": "dmf", "metric": FN, "columns": ["AMOUNT"]}
    assert executed == [
        build_custom_dmf_statement(
            {"function": FN, "columns": ["AMOUNT"]}, table="ORDERS", schema="RETAIL"
        )
    ]


def test_a_null_result_is_an_error_not_a_pass() -> None:
    outcome = _evaluate(lambda stmt: None)
    assert outcome.errored and not outcome.success
    assert outcome.metric_value is None
    assert "NULL" in outcome.error_message


def test_a_refused_name_never_reaches_the_warehouse() -> None:
    calls: list[Any] = []
    outcome = _evaluate(
        lambda *a: calls.append(a), {"function": "DB.S.F; DROP TABLE T", "columns": ["A"]}
    )
    assert outcome.errored
    assert calls == []
    assert "fully qualified name" in outcome.error_message


def test_custom_dmfs_have_no_derived_dimension() -> None:
    # ADR 0038: an arbitrary metric's quality aspect is unknowable — NULL renders as a coverage gap.
    assert derive_dimension(expectation_type="dmf:custom", kind="expectation") is None


# ── error classification: the live Snowflake shapes (2026-09-29, a non-admin role) ──


def _raising(message: str) -> Any:
    def fetch(statement: str) -> Any:
        raise RuntimeError(message + f"\n[SQL: {statement}]")

    return fetch


@pytest.mark.parametrize(
    ("driver_text", "expected"),
    [
        (
            # A DMF the role has no USAGE on reads exactly like one that does not exist.
            "002141 (42601): SQL compilation error:\n"
            "Unknown user-defined function DATAQ_DB.QUALITY.NEG_AMOUNT.",
            "does not exist, or the connection's role cannot use it",
        ),
        (
            "001044 (42P13): SQL compilation error: error line 1 at position 7\n"
            "Invalid argument types for function 'NEG_AMOUNT': (ROW(NUMBER(10,2), NUMBER(10,2)))",
            "don't match the custom DMF's signature",
        ),
        (
            "000904 (42000): SQL compilation error: error line 1 at position 66\n"
            "invalid identifier 'NOPE'",
            "a configured column does not exist",
        ),
        (
            "002003 (02000): SQL compilation error:\nSchema 'DATAQ_DB.QUALITY' does not exist "
            "or not authorized.",
            "custom DMF's schema, does not exist",
        ),
        (
            "Unsupported feature 'Data Metric Functions'. Requires Enterprise Edition.",
            "Enterprise Edition",
        ),
    ],
)
def test_live_failure_shapes_get_fixed_guidance_and_no_driver_text(
    driver_text: str, expected: str
) -> None:
    outcome = _evaluate(_raising(driver_text))
    assert outcome.errored
    assert expected in outcome.error_message
    assert "SQL compilation error" not in outcome.error_message
    assert "SELECT" not in outcome.error_message


def test_an_unrecognised_failure_falls_through_to_the_safe_classifier() -> None:
    outcome = _evaluate(_raising("something odd happened near SELECT secret_col"))
    assert outcome.errored
    assert "secret_col" not in outcome.error_message


# ── connection-test listing ──


def _row(catalog: str, schema: str, name: str, arguments: str | None = None) -> dict[str, Any]:
    # SHOW DATA METRIC FUNCTIONS columns, as SQLAlchemy's `.mappings()` keys them (live-verified).
    return {
        "catalog_name": catalog,
        "schema_name": schema,
        "name": name,
        "arguments": arguments if arguments is not None else f"{name}(TABLE(NUMBER)) RETURN NUMBER",
        "is_builtin": "N",
    }


def test_listing_keeps_custom_dmfs_and_drops_the_system_ones() -> None:
    executed: list[str] = []
    rows = [
        _row("SNOWFLAKE", "CORE", "NULL_COUNT"),
        _row("DATAQ_DB", "QUALITY", "NEG_AMOUNT"),
        _row("DATAQ_DB", "QUALITY", "GAP", "GAP(TABLE(NUMBER, NUMBER)) RETURN NUMBER"),
    ]
    result = list_custom_dmfs(_recording(executed, rows))
    assert executed == ["SHOW DATA METRIC FUNCTIONS IN ACCOUNT"]
    assert result == {
        "custom_functions": [
            {"name": "DATAQ_DB.QUALITY.GAP", "signature": "(TABLE(NUMBER, NUMBER)) RETURN NUMBER"},
            {"name": "DATAQ_DB.QUALITY.NEG_AMOUNT", "signature": "(TABLE(NUMBER)) RETURN NUMBER"},
        ],
        "custom_functions_truncated": False,
    }


def test_an_empty_listing_is_an_empty_list_not_a_failure() -> None:
    result = list_custom_dmfs(lambda stmt: [_row("SNOWFLAKE", "CORE", "NULL_COUNT")])
    assert result == {"custom_functions": [], "custom_functions_truncated": False}


def test_overloads_are_listed_once_per_signature() -> None:
    rows = [
        _row("D", "S", "F", "F(TABLE(NUMBER)) RETURN NUMBER"),
        _row("D", "S", "F", "F(TABLE(VARCHAR)) RETURN NUMBER"),
        _row("D", "S", "F", "F(TABLE(NUMBER)) RETURN NUMBER"),
    ]
    functions = list_custom_dmfs(lambda stmt: rows)["custom_functions"]
    assert [f["signature"] for f in functions] == [
        "(TABLE(NUMBER)) RETURN NUMBER",
        "(TABLE(VARCHAR)) RETURN NUMBER",
    ]


def test_names_a_check_could_not_reference_are_counted_not_listed() -> None:
    rows = [
        _row("D", "S", "OK_1"),
        _row("D", "S", "has space"),
        _row("D", "S", "quoted_lower"),  # would fold to QUOTED_LOWER and miss
        _row("D", "S", "Mixed_Case"),
    ]
    result = list_custom_dmfs(lambda stmt: rows)
    assert [f["name"] for f in result["custom_functions"]] == ["D.S.Mixed_Case", "D.S.OK_1"]
    assert result["custom_functions_unlisted"] == 2


def test_listing_is_capped_and_says_so() -> None:
    rows = [_row("D", "S", f"F{i:04d}") for i in range(MAX_LISTED_CUSTOM_DMFS + 5)]
    result = list_custom_dmfs(lambda stmt: rows)
    assert len(result["custom_functions"]) == MAX_LISTED_CUSTOM_DMFS
    assert result["custom_functions_truncated"] is True


def test_a_listing_failure_is_null_with_a_reason_never_a_raise() -> None:
    def boom(statement: str) -> Any:
        raise RuntimeError("SQL access control error: secret detail")

    result = list_custom_dmfs(boom)
    assert result["custom_functions"] is None
    assert "secret detail" not in result["custom_functions_reason"]
