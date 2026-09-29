"""Worked examples for the check-type reference: one config per check type, run on a sample.

`EXAMPLES` holds each check type's config exactly as the REST API, MCP and suite import take it.
The GX examples are evaluated on `SAMPLE_ROWS` by DataQ's own GX path, and their outcomes are
recorded in `check_examples.results.json` by `python scripts/docs/check_examples.py --record`;
`backend/tests/docs/test_check_examples.py` re-runs them and fails if a recorded outcome no
longer matches, so the published results cannot drift from what the product does.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RESULTS = Path(__file__).with_name("check_examples.results.json")

#: The sample every example runs on: ten orders, each column carrying one deliberate defect.
SAMPLE_COLUMNS = [
    "order_id",
    "customer_email",
    "status",
    "amount",
    "discount",
    "subtotal",
    "tax",
    "total",
    "country",
    "sku",
    "ordered_at",
    "shipped_at",
    "payload",
    "legacy_code",
    "invoice_total",
]
SAMPLE_ROWS: list[list[Any]] = [
    [
        1,
        "ana@example.com",
        "shipped",
        120.0,
        10.0,
        90.0,
        10.0,
        100.0,
        "US",
        "SKU-0001",
        "2026-09-20 09:15:00",
        "2026-09-21 10:00:00",
        '{"gift": false}',
        None,
    ],
    [
        2,
        "bo@example.com",
        "pending",
        75.5,
        0.0,
        80.0,
        20.0,
        100.0,
        "GB",
        "SKU-0002",
        "2026-09-21 14:02:00",
        "2026-09-22 08:30:00",
        '{"gift": true}',
        None,
    ],
    [
        3,
        "chen@example.com",
        "shipped",
        310.0,
        25.0,
        70.0,
        30.0,
        100.0,
        "IN",
        "SKU-0003",
        "2026-09-22 18:45:00",
        "2026-09-23 12:00:00",
        "{}",
        None,
    ],
    [
        4,
        None,
        "cancelled",
        42.0,
        0.0,
        95.0,
        5.0,
        100.0,
        "DE",
        "SKU-0004",
        "2026-09-23 07:30:00",
        "2026-09-24 09:00:00",
        '{"gift": false}',
        None,
    ],
    [
        5,
        "dev@example.com",
        "refunded",
        18.25,
        2.0,
        60.0,
        40.0,
        100.0,
        "US",
        "SKU-0005",
        "2026-09-24 11:11:00",
        "2026-09-25 16:20:00",
        '{"gift": false}',
        None,
    ],
    [
        6,
        "eli@example.com",
        "shipped",
        -5.0,
        0.0,
        85.0,
        15.0,
        100.0,
        "USA",
        "SKU-0006",
        "2026-09-25 22:05:00",
        "2026-09-26 07:45:00",
        '{"gift": true}',
        None,
    ],
    [
        7,
        "not-an-email",
        "pending",
        99.99,
        150.0,
        50.0,
        50.0,
        100.0,
        "FR",
        "SKU-0007",
        "2026-09-26 13:40:00",
        "2026-09-27 10:10:00",
        '{"gift": false}',
        None,
    ],
    [
        8,
        "fay@example.com",
        None,
        250.0,
        20.0,
        99.0,
        1.0,
        100.0,
        "GB",
        "sku_8",
        "2026-09-27 08:00:00",
        "2026-09-27 06:00:00",
        "{bad json",
        None,
    ],
    [
        9,
        "gus@example.com",
        "shipped",
        60.0,
        5.0,
        75.0,
        25.0,
        100.0,
        "IN",
        "SKU-0009",
        "2026-09-27 19:25:00",
        "2026-09-28 09:30:00",
        '{"gift": false}',
        None,
    ],
    [
        7,
        "hal@example.com",
        "shipped",
        88.0,
        8.0,
        40.0,
        50.0,
        90.0,
        "US",
        "SKU-0010",
        "2026/09/28 06:00",
        "2026-09-28 18:00:00",
        '{"gift": true}',
        None,
    ],
]


@dataclass(frozen=True)
class Example:
    """One worked example: the config as the API takes it, and what the reader should notice."""

    config: dict[str, Any]
    note: str
    thresholds: dict[str, float] = field(default_factory=dict)
    #: False for types that run only on a warehouse engine (DMF, DQX), on SQL (custom SQL), or
    #: need a second dataset (comparison); their result is described in `note`.
    runnable: bool = True
    kind: str = "expectation"
    engine: str = "gx"
    #: The connection type the example is authored against in the validation test.
    connection: str = "postgres"


def _gx(config: dict[str, Any], note: str, **thresholds: float) -> Example:
    return Example(config=config, note=note, thresholds=thresholds)


EXAMPLES: dict[str, Example] = {
    "expect_column_values_to_not_be_null": _gx(
        {"column": "customer_email"},
        "Order 4 has no email: 1 of 10 rows (10%).",
        warn_threshold=0,
        fail_threshold=5,
    ),
    "expect_column_values_to_be_unique": _gx(
        {"column": "order_id"}, "Order id 7 appears twice, so both rows are unexpected."
    ),
    "expect_column_values_to_be_between": _gx(
        {"column": "amount", "min_value": 0, "max_value": 1000},
        "Order 6 has a negative amount.",
    ),
    "expect_column_values_to_be_in_set": _gx(
        {"column": "status", "value_set": ["pending", "shipped", "cancelled"]},
        "`refunded` is not in the allowed set: 1 of the 9 non-NULL values (GX skips a NULL).",
    ),
    "expect_column_values_to_be_null": _gx(
        {"column": "legacy_code"}, "A retired column that must stay empty: every row is NULL."
    ),
    "expect_column_values_to_not_be_in_set": _gx(
        {"column": "status", "value_set": ["refunded", "unknown"]},
        "One order is `refunded`, a status that should never reach this table.",
    ),
    "expect_column_value_lengths_to_be_between": _gx(
        {"column": "sku", "min_value": 8, "max_value": 8}, "`sku_8` is five characters."
    ),
    "expect_column_value_lengths_to_equal": _gx(
        {"column": "country", "value": 2}, "`USA` is not an ISO 3166 alpha-2 code."
    ),
    "expect_column_values_to_match_regex": _gx(
        {"column": "sku", "regex": r"^SKU-\d{4}$"}, "`sku_8` doesn't match the SKU pattern."
    ),
    "expect_column_values_to_not_match_regex": _gx(
        {"column": "customer_email", "regex": r"^[^@]+$"},
        "A value with no `@` is not an email: `not-an-email`.",
    ),
    "expect_column_values_to_match_regex_list": _gx(
        {"column": "sku", "regex_list": [r"^SKU-\d{4}$", r"^LEGACY-\d+$"], "match_on": "any"},
        "Two accepted formats; `sku_8` matches neither.",
    ),
    "expect_column_values_to_not_match_regex_list": _gx(
        {"column": "customer_email", "regex_list": [r"@test\.", r"^not-"]},
        "A deny-list of placeholder addresses; `not-an-email` is caught.",
    ),
    "expect_column_values_to_be_json_parseable": _gx(
        {"column": "payload"}, "Order 8's payload is truncated JSON."
    ),
    "expect_column_values_to_be_of_type": _gx(
        {"column": "amount", "type_": "float64"},
        "The sample is a pandas frame, so the dtype name is `float64` (see the dtype table in "
        "the datasources guide).",
    ),
    "expect_column_values_to_be_in_type_list": _gx(
        {"column": "order_id", "type_list": ["int64", "int32"]},
        "Either integer width is accepted.",
    ),
    "expect_compound_columns_to_be_unique": _gx(
        {"column_list": ["order_id", "customer_email"]},
        "The duplicated order id carries two different emails, so the pair is still unique.",
    ),
    "expect_column_pair_values_a_to_be_greater_than_b": _gx(
        {"column_A": "shipped_at", "column_B": "ordered_at", "or_equal": True},
        "Order 8 shipped before it was ordered. These are text columns, so the last row, whose "
        "date is written `2026/09/28 06:00`, also compares out of order: parse dates upstream.",
    ),
    "expect_column_pair_values_to_be_equal": _gx(
        {"column_A": "total", "column_B": "invoice_total"},
        "Order 4 was invoiced 5 more than its total, so 1 row of 10 fails.",
    ),
    "expect_select_column_values_to_be_unique_within_record": _gx(
        {"column_list": ["subtotal", "tax"]},
        "Order 7 has subtotal and tax both 50.",
    ),
    "expect_multicolumn_sum_to_equal": _gx(
        {"column_list": ["subtotal", "tax"], "sum_total": 100},
        "The last row's parts sum to 90.",
    ),
    "expect_column_distinct_values_to_be_in_set": _gx(
        {"column": "country", "value_set": ["US", "GB", "IN", "DE", "FR"]},
        "`USA` is outside the set. There is no unexpected-%: the check passes or fails on the "
        "set of distinct values, and the failing sample lists the ones outside it.",
    ),
    "expect_column_distinct_values_to_contain_set": _gx(
        {"column": "country", "value_set": ["US", "GB", "JP"]},
        "No order comes from Japan.",
    ),
    "expect_column_values_to_match_strftime_format": _gx(
        {"column": "ordered_at", "strftime_format": "%Y-%m-%d %H:%M:%S"},
        "The last row was written as `2026/09/28 06:00`.",
    ),
    "expect_table_row_count_to_be_between": _gx(
        {"min_value": 5, "max_value": 1000}, "Ten rows is inside the band."
    ),
}


def _described(
    config: dict[str, Any],
    note: str,
    *,
    kind: str = "expectation",
    engine: str = "gx",
    connection: str = "postgres",
    **thresholds: float,
) -> Example:
    return Example(
        config=config,
        note=note,
        thresholds=thresholds,
        runnable=False,
        kind=kind,
        engine=engine,
        connection=connection,
    )


EXAMPLES.update(
    {
        "monitor:freshness": _described(
            {"column": "shipped_at"},
            "Reports the newest `shipped_at` and its age in hours as the metric; with these "
            "thresholds, 24 h old or more warns and 48 h fails. Leave `column` out on a flat "
            "file to measure the file's arrival time instead.",
            kind="freshness",
            warn_threshold=24,
            fail_threshold=48,
        ),
        "monitor:volume": _described(
            {"min_rows": 5, "max_rows": 1000},
            "Reports the row count as the metric; outside the band fails. On the sample, 10 "
            "rows passes.",
            kind="volume",
        ),
        "monitor:schema_drift": _described(
            {"ignore_columns": ["legacy_code"]},
            "The first run captures the columns and their types as the baseline; later runs "
            "report every added, dropped or retyped column. `legacy_code` is never compared.",
            kind="schema_drift",
        ),
        "monitor:anomaly": _described(
            {"target_metric": "row_count", "window": 14, "min_points": 7, "seasonality": True},
            "Learns the table's row count over the last 14 runs, compared with the same weekday "
            "when `seasonality` is on, and reports the z-score. It skips until 7 runs exist.",
            kind="anomaly",
            warn_threshold=2,
            fail_threshold=3,
        ),
        "unexpected_rows_expectation": _described(
            {"unexpected_rows_query": "SELECT * FROM {batch} WHERE discount > amount"},
            "Every row the query returns is unexpected. On the sample, order 7's discount "
            "(150) exceeds its amount, so 1 row fails. `{batch}` is the suite's target.",
        ),
        "comparison:records": _described(
            {
                "source": {"table": "orders_staging", "schema": "public"},
                "keys": ["order_id"],
            },
            "Joins the suite's target to `orders_staging` on `order_id` and reports matched, "
            "mismatched and missing rows on each side; set `source_connection_id` to the "
            "connection holding the source.",
            kind="comparison",
        ),
        "comparison:columns": _described(
            {
                "source": {"table": "orders_staging", "schema": "public"},
                "keys": ["order_id"],
                "columns": ["amount", "status"],
            },
            "Like the record comparison, but compares only the listed columns and reports the "
            "mismatch rate for each.",
            kind="comparison",
        ),
        "dmf:null_count": _described(
            {"column": "CUSTOMER_EMAIL"},
            "Snowflake counts the NULLs itself (`SNOWFLAKE.CORE.NULL_COUNT`); the count is the "
            "metric. On the sample it would be 1.",
            engine="dmf",
            connection="snowflake",
            fail_threshold=1,
        ),
        "dmf:null_percent": _described(
            {"column": "CUSTOMER_EMAIL"},
            "The NULL percentage (10 on the sample).",
            engine="dmf",
            connection="snowflake",
            warn_threshold=1,
            fail_threshold=5,
        ),
        "dmf:duplicate_count": _described(
            {"column": "ORDER_ID"},
            "Rows whose value repeats another row's (2 on the sample: both order 7s).",
            engine="dmf",
            connection="snowflake",
            fail_threshold=1,
        ),
        "dmf:blank_count": _described(
            {"column": "STATUS"},
            "Empty or whitespace-only strings; a NULL is not blank.",
            engine="dmf",
            connection="snowflake",
            fail_threshold=1,
        ),
        "dmf:future_timestamp_percent": _described(
            {"column": "SHIPPED_AT"},
            "The percentage of timestamps later than now; takes DATE, TIMESTAMP_LTZ and "
            "TIMESTAMP_TZ columns only.",
            engine="dmf",
            connection="snowflake",
            fail_threshold=0.01,
        ),
        "dmf:accepted_values": _described(
            {"column": "STATUS", "value_set": ["pending", "shipped", "cancelled"]},
            "Values outside the set.",
            engine="dmf",
            connection="snowflake",
            fail_threshold=1,
        ),
        "dmf:unique_count": _described(
            {"column": "ORDER_ID"},
            "The number of distinct non-NULL values (9 on the sample).",
            engine="dmf",
            connection="snowflake",
        ),
        "dmf:custom": _described(
            {"function": "ANALYTICS.DQ.NEGATIVE_AMOUNTS", "columns": ["AMOUNT"]},
            "Calls your own data metric function over the listed columns; whatever it returns "
            "is the metric, banded by the thresholds.",
            engine="dmf",
            connection="snowflake",
            fail_threshold=1,
        ),
        "dqx:is_not_null": _described(
            {"column": "customer_email"},
            "Databricks DQX counts failing rows in your workspace; the failing count is the "
            "metric (1 on the sample).",
            engine="dqx",
            connection="unity_catalog",
            fail_threshold=1,
        ),
        "dqx:is_not_empty": _described(
            {"column": "status"},
            "Rows whose value is an empty string.",
            engine="dqx",
            connection="unity_catalog",
            fail_threshold=1,
        ),
        "dqx:is_not_null_and_not_empty": _described(
            {"column": "status", "mode": "stream"},
            "NULL or empty. `mode: stream` evaluates only rows appended since the last run.",
            engine="dqx",
            connection="unity_catalog",
            fail_threshold=1,
        ),
        "dqx:is_in_list": _described(
            {"column": "status", "allowed": ["pending", "shipped", "cancelled"]},
            "Rows outside the list (`refunded` on the sample).",
            engine="dqx",
            connection="unity_catalog",
            fail_threshold=1,
        ),
        "dqx:is_in_range": _described(
            {"column": "amount", "min_limit": 0, "max_limit": 1000},
            "Rows outside [0, 1000] (order 6).",
            engine="dqx",
            connection="unity_catalog",
            fail_threshold=1,
        ),
        "dqx:regex_match": _described(
            {"column": "sku", "regex": "^SKU-[0-9]{4}$"},
            "Rows not matching (`sku_8`).",
            engine="dqx",
            connection="unity_catalog",
            fail_threshold=1,
        ),
        "dqx:is_not_less_than": _described(
            {"column": "amount", "limit": 0},
            "Rows below the limit.",
            engine="dqx",
            connection="unity_catalog",
            fail_threshold=1,
        ),
        "dqx:is_not_greater_than": _described(
            {"column": "discount", "limit": 100},
            "Rows above the limit (order 7's discount of 150).",
            engine="dqx",
            connection="unity_catalog",
            fail_threshold=1,
        ),
    }
)


# The invoice agrees with the order total except on order 4.
for _row in SAMPLE_ROWS:
    _row.append(_row[7] + (5.0 if _row[0] == 4 else 0.0))


def sample_frame() -> Any:
    import pandas as pd

    return pd.DataFrame(SAMPLE_ROWS, columns=SAMPLE_COLUMNS)


def evaluate() -> dict[str, dict[str, Any]]:
    """Run every runnable GX example on the sample through DataQ's GX path."""
    sys.path.insert(0, str(ROOT))
    from backend.app.datasources.base import CheckSpec
    from backend.app.datasources.gx_runner import ephemeral_gx_context, run_expectations
    from backend.app.services.severity import resolve_status

    types = [
        t
        for t, ex in EXAMPLES.items()
        if ex.runnable and not t.startswith(("monitor:", "dmf:", "dqx:", "comparison:"))
    ]
    specs = [CheckSpec(expectation_type=t, kwargs=dict(EXAMPLES[t].config)) for t in types]
    with ephemeral_gx_context() as context:
        asset = context.data_sources.add_pandas(name="docs").add_dataframe_asset(name="orders")
        batch = asset.add_batch_definition_whole_dataframe(name="whole")
        outcome = run_expectations(
            context,
            batch_definition=batch,
            checks=specs,
            name="docs-examples",
            batch_parameters={"dataframe": sample_frame()},
        )
    results: dict[str, dict[str, Any]] = {}
    for t, check in zip(types, outcome.checks, strict=True):
        th = EXAMPLES[t].thresholds
        status, metric = resolve_status(
            check,
            warn_threshold=Decimal(str(th["warn_threshold"])) if "warn_threshold" in th else None,
            fail_threshold=Decimal(str(th["fail_threshold"])) if "fail_threshold" in th else None,
            critical_threshold=(
                Decimal(str(th["critical_threshold"])) if "critical_threshold" in th else None
            ),
        )
        samples = check.sample_failures or {}
        values = samples.get("partial_unexpected_list")
        if values is not None:
            values = [None if v != v else v for v in values]  # NaN is a NULL, and not JSON
        results[t] = {
            "status": status,
            "metric_value": None if metric is None else round(float(metric), 2),
            "observed_value": (check.observed_value or {}).get("observed_value"),
            "unexpected_count": samples.get("unexpected_count"),
            "unexpected_values": values,
        }
    return results


if __name__ == "__main__":
    if "--record" in sys.argv:
        RESULTS.write_text(json.dumps(evaluate(), indent=2, sort_keys=True, default=str) + "\n")
        print(f"wrote {RESULTS.relative_to(ROOT)}")
    else:
        print(json.dumps(evaluate(), indent=2, sort_keys=True, default=str))
