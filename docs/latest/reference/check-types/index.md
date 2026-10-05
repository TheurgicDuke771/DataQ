# Check types

Every kind of check DataQ can author, generated from the check editor's catalog and the
backend's vetted allowlist — so this page cannot drift from what the product actually
offers. Every GX type on this page is executed in CI on a dataframe batch, and on a SQL batch too unless its row says it is dataframe-only.

| | Count |
|---|---|
| Check types in the editor | 48 |
| GX expectation types vetted by the backend | 25 |

How to read a row: **Parameters** are the editor's fields (`mostly` is GX's optional row
tolerance, a fraction). **Thresholds** are the severity bands read from the result.
**Dimension** is the default data-quality dimension the check is classified under; you can
change it on any check.

Each section ends with **examples**: the body you would send to create the check (`POST /api/v1/suites/{suite_id}/checks`; the same fields work over MCP and in a suite import) and, for every type that runs on a plain table, the result DataQ reports on the sample below. Those results are produced by DataQ's own check engine and re-checked in CI.

??? info "The sample table every example below runs on"

    Ten orders, with one deliberate defect in most columns.

    | order_id | customer_email | status | amount | discount | subtotal | tax | total | country | sku | ordered_at | shipped_at | payload | legacy_code | invoice_total |
    |---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
    | 1 | ana@example.com | shipped | 120.0 | 10.0 | 90.0 | 10.0 | 100.0 | US | SKU-0001 | 2026-09-20 09:15:00 | 2026-09-21 10:00:00 | {"gift": false} | NULL | 100.0 |
    | 2 | bo@example.com | pending | 75.5 | 0.0 | 80.0 | 20.0 | 100.0 | GB | SKU-0002 | 2026-09-21 14:02:00 | 2026-09-22 08:30:00 | {"gift": true} | NULL | 100.0 |
    | 3 | chen@example.com | shipped | 310.0 | 25.0 | 70.0 | 30.0 | 100.0 | IN | SKU-0003 | 2026-09-22 18:45:00 | 2026-09-23 12:00:00 | {} | NULL | 100.0 |
    | 4 | NULL | cancelled | 42.0 | 0.0 | 95.0 | 5.0 | 100.0 | DE | SKU-0004 | 2026-09-23 07:30:00 | 2026-09-24 09:00:00 | {"gift": false} | NULL | 105.0 |
    | 5 | dev@example.com | refunded | 18.25 | 2.0 | 60.0 | 40.0 | 100.0 | US | SKU-0005 | 2026-09-24 11:11:00 | 2026-09-25 16:20:00 | {"gift": false} | NULL | 100.0 |
    | 6 | eli@example.com | shipped | -5.0 | 0.0 | 85.0 | 15.0 | 100.0 | USA | SKU-0006 | 2026-09-25 22:05:00 | 2026-09-26 07:45:00 | {"gift": true} | NULL | 100.0 |
    | 7 | not-an-email | pending | 99.99 | 150.0 | 50.0 | 50.0 | 100.0 | FR | SKU-0007 | 2026-09-26 13:40:00 | 2026-09-27 10:10:00 | {"gift": false} | NULL | 100.0 |
    | 8 | fay@example.com | NULL | 250.0 | 20.0 | 99.0 | 1.0 | 100.0 | GB | sku_8 | 2026-09-27 08:00:00 | 2026-09-27 06:00:00 | {bad json | NULL | 100.0 |
    | 9 | gus@example.com | shipped | 60.0 | 5.0 | 75.0 | 25.0 | 100.0 | IN | SKU-0009 | 2026-09-27 19:25:00 | 2026-09-28 09:30:00 | {"gift": false} | NULL | 100.0 |
    | 7 | hal@example.com | shipped | 88.0 | 8.0 | 40.0 | 50.0 | 90.0 | US | SKU-0010 | 2026/09/28 06:00 | 2026-09-28 18:00:00 | {"gift": true} | NULL | 90.0 |

## Column values

Great Expectations built-ins that look at the values in one or more columns. Each returns an unexpected-% that the warn / fail / critical severity bands read.

| Check | Type | What it checks | Dimension | Parameters | Thresholds | Runs on |
|---|---|---|---|---|---|---|
| **Column values not null** | `expect_column_values_to_not_be_null` | Every value in the column is non-null. | Completeness | `column`, `mostly` *(optional)* | warn / fail / critical | All datasources · SQL pushdown on Unity Catalog |
| **Column values unique** | `expect_column_values_to_be_unique` | Values in the column are distinct (no duplicates). | Uniqueness | `column`, `mostly` *(optional)* | warn / fail / critical | All datasources · SQL pushdown on Unity Catalog |
| **Column values in range** | `expect_column_values_to_be_between` | Numeric values fall within [min, max]. | Validity | `column`, `min_value` *(optional)*, `max_value` *(optional)*, `mostly` *(optional)* | warn / fail / critical | All datasources · SQL pushdown on Unity Catalog |
| **Column values in set** | `expect_column_values_to_be_in_set` | Every value is one of an allowed set. | Validity | `column`, `value_set`, `mostly` *(optional)* | warn / fail / critical | All datasources · SQL pushdown on Unity Catalog |
| **Column values null** | `expect_column_values_to_be_null` | Every value in the column is null — for a deprecated or not-yet-populated column that should stay empty. The inverse of “Column values not null”. | Validity | `column`, `mostly` *(optional)* | warn / fail / critical | All datasources · SQL pushdown on Unity Catalog |
| **Column values not in set** | `expect_column_values_to_not_be_in_set` | No value is one of a forbidden set — e.g. a status that should never reach this table, or placeholder values like “N/A” and “UNKNOWN”. | Validity | `column`, `value_set`, `mostly` *(optional)* | warn / fail / critical | All datasources · SQL pushdown on Unity Catalog |
| **Column value lengths in range** | `expect_column_value_lengths_to_be_between` | String lengths fall within [min, max]. | Validity | `column`, `min_value` *(optional)*, `max_value` *(optional)*, `mostly` *(optional)* | warn / fail / critical | All datasources · SQL pushdown on Unity Catalog |
| **Column value lengths equal** | `expect_column_value_lengths_to_equal` | Every value is exactly the given number of characters — for a fixed-width code (ISO country, SKU, account number). | Validity | `column`, `value`, `mostly` *(optional)* | warn / fail / critical | All datasources · SQL pushdown on Unity Catalog |
| **Column values match regex** | `expect_column_values_to_match_regex` | Every value matches the given regular expression. | Validity | `column`, `regex`, `mostly` *(optional)* | warn / fail / critical | Snowflake, ADLS Gen2, AWS S3, Unity Catalog, Apache Iceberg, PostgreSQL, MySQL / MariaDB, Trino, Amazon Athena, Amazon Redshift — not SQL Server (no translation for that SQL dialect; refused at author time) · SQL pushdown on Unity Catalog |
| **Column values do not match regex** | `expect_column_values_to_not_match_regex` | No value matches the given regular expression — for catching a pattern that should never appear (a stray delimiter, an unredacted identifier). | Validity | `column`, `regex`, `mostly` *(optional)* | warn / fail / critical | Snowflake, ADLS Gen2, AWS S3, Unity Catalog, Apache Iceberg, PostgreSQL, MySQL / MariaDB, Trino, Amazon Athena, Amazon Redshift — not SQL Server (no translation for that SQL dialect; refused at author time) · SQL pushdown on Unity Catalog |
| **Column values match a list of regexes** | `expect_column_values_to_match_regex_list` | Every value matches the regexes in the list — by default ANY one of them is enough, for a column carrying several legitimate formats (e.g. two phone-number conventions). | Validity | `column`, `regex_list`, `match_on` *(optional)*, `mostly` *(optional)* | warn / fail / critical | Snowflake, ADLS Gen2, AWS S3, Unity Catalog, Apache Iceberg, PostgreSQL, MySQL / MariaDB, Trino, Amazon Athena, Amazon Redshift — not SQL Server (no translation for that SQL dialect; refused at author time) · SQL pushdown on Unity Catalog |
| **Column values match none of a list of regexes** | `expect_column_values_to_not_match_regex_list` | No value matches ANY regex in the list — a deny-list of forbidden formats. | Validity | `column`, `regex_list`, `mostly` *(optional)* | warn / fail / critical | Snowflake, ADLS Gen2, AWS S3, Unity Catalog, Apache Iceberg, PostgreSQL, MySQL / MariaDB, Trino, Amazon Athena, Amazon Redshift — not SQL Server (no translation for that SQL dialect; refused at author time) · SQL pushdown on Unity Catalog |
| **Column values are valid JSON** | `expect_column_values_to_be_json_parseable` | Every value parses as JSON — for a payload/metadata column stored as text. Not offered on Snowflake, PostgreSQL, MySQL, Trino, SQL Server, Athena or Redshift: Great Expectations implements this one only for dataframe batches, so a SQL batch would error on every run. Use a custom-SQL check there (or, on Snowflake, a VARIANT column). | Validity | `column`, `mostly` *(optional)* | warn / fail / critical | ADLS Gen2, AWS S3, Unity Catalog, Apache Iceberg — not Snowflake, PostgreSQL, MySQL / MariaDB, Trino, SQL Server, Amazon Athena, Amazon Redshift (no SQL implementation; refused at author time) |
| **Column values are of type** | `expect_column_values_to_be_of_type` | Every value in the column matches the given data type. | Validity | `column`, `type_`, `mostly` *(optional)* | warn / fail / critical | All datasources |
| **Column values are of one of several types** | `expect_column_values_to_be_in_type_list` | Every value in the column matches at least one of the given data types — the tolerant sibling of “Column values are of type”, for a column whose type legitimately varies by datasource or load. | Validity | `column`, `type_list`, `mostly` *(optional)* | warn / fail / critical | All datasources |
| **Compound columns unique** | `expect_compound_columns_to_be_unique` | The COMBINATION of values across the listed columns is distinct on every row — a multi-column primary or business key. Each column on its own may repeat freely. | Uniqueness | `column_list`, `mostly` *(optional)* | warn / fail / critical | All datasources · SQL pushdown on Unity Catalog |
| **Column A greater than column B** | `expect_column_pair_values_a_to_be_greater_than_b` | Row by row, column A is greater than column B — e.g. ended_at > started_at, or total >= discount. | Validity | `column_A`, `column_B`, `or_equal` *(optional)*, `mostly` *(optional)* | warn / fail / critical | All datasources · SQL pushdown on Unity Catalog |
| **Column A equals column B** | `expect_column_pair_values_to_be_equal` | Row by row, the two columns hold the same value — e.g. a denormalised copy that must agree with its source, or a total that must match a recomputed one. | Validity | `column_A`, `column_B`, `mostly` *(optional)* | warn / fail / critical | All datasources · SQL pushdown on Unity Catalog |
| **Values unique within each row** | `expect_select_column_values_to_be_unique_within_record` | Within a single row, the listed columns all hold different values — e.g. a transfer whose source and destination account must not be the same. This is per-row; use “Compound columns unique” for uniqueness ACROSS rows. | Uniqueness | `column_list`, `mostly` *(optional)* | warn / fail / critical | All datasources · SQL pushdown on Unity Catalog |
| **Columns sum to a total** | `expect_multicolumn_sum_to_equal` | Row by row, the listed columns add up to the given total — e.g. subtotal + tax + shipping = total. | Validity | `column_list`, `sum_total`, `mostly` *(optional)* | warn / fail / critical | All datasources · SQL pushdown on Unity Catalog |
| **Column distinct values in set** | `expect_column_distinct_values_to_be_in_set` | Every DISTINCT value present in the column is one of an allowed set — reports WHICH unexpected values exist rather than how many rows carry them. Use “Column values in set” when you care about the row count. | Validity | `column`, `value_set` | None — pass/fail only | All datasources · SQL pushdown on Unity Catalog |
| **Column distinct values contain set** | `expect_column_distinct_values_to_contain_set` | Every value in the given set appears at least once in the column — catches a category that stopped arriving. The column may also contain other values. | Completeness | `column`, `value_set` | None — pass/fail only | All datasources · SQL pushdown on Unity Catalog |
| **Column values match a date format** | `expect_column_values_to_match_strftime_format` | Every value parses under the given strftime format — for a date or timestamp stored as text. Not offered on Snowflake, PostgreSQL, MySQL, Trino, SQL Server, Athena or Redshift: Great Expectations implements this one only for dataframe batches, so a SQL batch would error on every run. Use a custom-SQL check there. | Validity | `column`, `strftime_format`, `mostly` *(optional)* | warn / fail / critical | ADLS Gen2, AWS S3, Unity Catalog, Apache Iceberg — not Snowflake, PostgreSQL, MySQL / MariaDB, Trino, SQL Server, Amazon Athena, Amazon Redshift (no SQL implementation; refused at author time) |

### Examples

??? example "Column values not null"

    ```json
    {
      "name": "Column values not null",
      "expectation_type": "expect_column_values_to_not_be_null",
      "config": {
        "column": "customer_email"
      },
      "warn_threshold": 0,
      "fail_threshold": 5
    }
    ```

    **On the sample:** **FAIL** · 10% unexpected (1 row) · unexpected values `[null]`

    Order 4 has no email: 1 of 10 rows (10%).

??? example "Column values unique"

    ```json
    {
      "name": "Column values unique",
      "expectation_type": "expect_column_values_to_be_unique",
      "config": {
        "column": "order_id"
      }
    }
    ```

    **On the sample:** **FAIL** · 20% unexpected (2 rows) · unexpected values `[7, 7]`

    Order id 7 appears twice, so both rows are unexpected.

??? example "Column values in range"

    ```json
    {
      "name": "Column values in range",
      "expectation_type": "expect_column_values_to_be_between",
      "config": {
        "column": "amount",
        "min_value": 0,
        "max_value": 1000
      }
    }
    ```

    **On the sample:** **FAIL** · 10% unexpected (1 row) · unexpected values `[-5.0]`

    Order 6 has a negative amount.

??? example "Column values in set"

    ```json
    {
      "name": "Column values in set",
      "expectation_type": "expect_column_values_to_be_in_set",
      "config": {
        "column": "status",
        "value_set": [
          "pending",
          "shipped",
          "cancelled"
        ]
      }
    }
    ```

    **On the sample:** **FAIL** · 11.11% unexpected (1 row) · unexpected values `["refunded"]`

    `refunded` is not in the allowed set: 1 of the 9 non-NULL values (GX skips a NULL).

??? example "Column values null"

    ```json
    {
      "name": "Column values null",
      "expectation_type": "expect_column_values_to_be_null",
      "config": {
        "column": "legacy_code"
      }
    }
    ```

    **On the sample:** **PASS** · 0% unexpected (0 rows)

    A retired column that must stay empty: every row is NULL.

??? example "Column values not in set"

    ```json
    {
      "name": "Column values not in set",
      "expectation_type": "expect_column_values_to_not_be_in_set",
      "config": {
        "column": "status",
        "value_set": [
          "refunded",
          "unknown"
        ]
      }
    }
    ```

    **On the sample:** **FAIL** · 11.11% unexpected (1 row) · unexpected values `["refunded"]`

    One order is `refunded`, a status that should never reach this table.

??? example "Column value lengths in range"

    ```json
    {
      "name": "Column value lengths in range",
      "expectation_type": "expect_column_value_lengths_to_be_between",
      "config": {
        "column": "sku",
        "min_value": 8,
        "max_value": 8
      }
    }
    ```

    **On the sample:** **FAIL** · 10% unexpected (1 row) · unexpected values `["sku_8"]`

    `sku_8` is five characters.

??? example "Column value lengths equal"

    ```json
    {
      "name": "Column value lengths equal",
      "expectation_type": "expect_column_value_lengths_to_equal",
      "config": {
        "column": "country",
        "value": 2
      }
    }
    ```

    **On the sample:** **FAIL** · 10% unexpected (1 row) · unexpected values `["USA"]`

    `USA` is not an ISO 3166 alpha-2 code.

??? example "Column values match regex"

    ```json
    {
      "name": "Column values match regex",
      "expectation_type": "expect_column_values_to_match_regex",
      "config": {
        "column": "sku",
        "regex": "^SKU-\\d{4}$"
      }
    }
    ```

    **On the sample:** **FAIL** · 10% unexpected (1 row) · unexpected values `["sku_8"]`

    `sku_8` doesn't match the SKU pattern.

??? example "Column values do not match regex"

    ```json
    {
      "name": "Column values do not match regex",
      "expectation_type": "expect_column_values_to_not_match_regex",
      "config": {
        "column": "customer_email",
        "regex": "^[^@]+$"
      }
    }
    ```

    **On the sample:** **FAIL** · 11.11% unexpected (1 row) · unexpected values `["not-an-email"]`

    A value with no `@` is not an email: `not-an-email`.

??? example "Column values match a list of regexes"

    ```json
    {
      "name": "Column values match a list of regexes",
      "expectation_type": "expect_column_values_to_match_regex_list",
      "config": {
        "column": "sku",
        "regex_list": [
          "^SKU-\\d{4}$",
          "^LEGACY-\\d+$"
        ],
        "match_on": "any"
      }
    }
    ```

    **On the sample:** **FAIL** · 10% unexpected (1 row) · unexpected values `["sku_8"]`

    Two accepted formats; `sku_8` matches neither.

??? example "Column values match none of a list of regexes"

    ```json
    {
      "name": "Column values match none of a list of regexes",
      "expectation_type": "expect_column_values_to_not_match_regex_list",
      "config": {
        "column": "customer_email",
        "regex_list": [
          "@test\\.",
          "^not-"
        ]
      }
    }
    ```

    **On the sample:** **FAIL** · 11.11% unexpected (1 row) · unexpected values `["not-an-email"]`

    A deny-list of placeholder addresses; `not-an-email` is caught.

??? example "Column values are valid JSON"

    ```json
    {
      "name": "Column values are valid JSON",
      "expectation_type": "expect_column_values_to_be_json_parseable",
      "config": {
        "column": "payload"
      }
    }
    ```

    **On the sample:** **FAIL** · 10% unexpected (1 row) · unexpected values `["{bad json"]`

    Order 8's payload is truncated JSON.

??? example "Column values are of type"

    ```json
    {
      "name": "Column values are of type",
      "expectation_type": "expect_column_values_to_be_of_type",
      "config": {
        "column": "amount",
        "type_": "float64"
      }
    }
    ```

    **On the sample:** **PASS** · observed `"float64"`

    The sample is a pandas frame, so the dtype name is `float64` (see the dtype table in the datasources guide).

??? example "Column values are of one of several types"

    ```json
    {
      "name": "Column values are of one of several types",
      "expectation_type": "expect_column_values_to_be_in_type_list",
      "config": {
        "column": "order_id",
        "type_list": [
          "int64",
          "int32"
        ]
      }
    }
    ```

    **On the sample:** **PASS** · observed `"int64"`

    Either integer width is accepted.

??? example "Compound columns unique"

    ```json
    {
      "name": "Compound columns unique",
      "expectation_type": "expect_compound_columns_to_be_unique",
      "config": {
        "column_list": [
          "order_id",
          "customer_email"
        ]
      }
    }
    ```

    **On the sample:** **PASS** · 0% unexpected (0 rows)

    The duplicated order id carries two different emails, so the pair is still unique.

??? example "Column A greater than column B"

    ```json
    {
      "name": "Column A greater than column B",
      "expectation_type": "expect_column_pair_values_a_to_be_greater_than_b",
      "config": {
        "column_A": "shipped_at",
        "column_B": "ordered_at",
        "or_equal": true
      }
    }
    ```

    **On the sample:** **FAIL** · 20% unexpected (2 rows) · unexpected values `[["2026-09-27 06:00:00", "2026-09-27 08:00:00"], ["2026-09-28 18:00:00", "2026/09/28 06:00"]]`

    Order 8 shipped before it was ordered. These are text columns, so the last row, whose date is written `2026/09/28 06:00`, also compares out of order: parse dates upstream.

??? example "Column A equals column B"

    ```json
    {
      "name": "Column A equals column B",
      "expectation_type": "expect_column_pair_values_to_be_equal",
      "config": {
        "column_A": "total",
        "column_B": "invoice_total"
      }
    }
    ```

    **On the sample:** **FAIL** · 10% unexpected (1 row) · unexpected values `[[100.0, 105.0]]`

    Order 4 was invoiced 5 more than its total, so 1 row of 10 fails.

??? example "Values unique within each row"

    ```json
    {
      "name": "Values unique within each row",
      "expectation_type": "expect_select_column_values_to_be_unique_within_record",
      "config": {
        "column_list": [
          "subtotal",
          "tax"
        ]
      }
    }
    ```

    **On the sample:** **FAIL** · 10% unexpected (1 row) · unexpected values `[{"subtotal": 50.0, "tax": 50.0}]`

    Order 7 has subtotal and tax both 50.

??? example "Columns sum to a total"

    ```json
    {
      "name": "Columns sum to a total",
      "expectation_type": "expect_multicolumn_sum_to_equal",
      "config": {
        "column_list": [
          "subtotal",
          "tax"
        ],
        "sum_total": 100
      }
    }
    ```

    **On the sample:** **FAIL** · 10% unexpected (1 row) · unexpected values `[{"subtotal": 40.0, "tax": 50.0}]`

    The last row's parts sum to 90.

??? example "Column distinct values in set"

    ```json
    {
      "name": "Column distinct values in set",
      "expectation_type": "expect_column_distinct_values_to_be_in_set",
      "config": {
        "column": "country",
        "value_set": [
          "US",
          "GB",
          "IN",
          "DE",
          "FR"
        ]
      }
    }
    ```

    **On the sample:** **FAIL** · unexpected values `["USA"]`

    `USA` is outside the set. There is no unexpected-%: the check passes or fails on the set of distinct values, and the failing sample lists the ones outside it.

??? example "Column distinct values contain set"

    ```json
    {
      "name": "Column distinct values contain set",
      "expectation_type": "expect_column_distinct_values_to_contain_set",
      "config": {
        "column": "country",
        "value_set": [
          "US",
          "GB",
          "JP"
        ]
      }
    }
    ```

    **On the sample:** **FAIL**

    No order comes from Japan.

??? example "Column values match a date format"

    ```json
    {
      "name": "Column values match a date format",
      "expectation_type": "expect_column_values_to_match_strftime_format",
      "config": {
        "column": "ordered_at",
        "strftime_format": "%Y-%m-%d %H:%M:%S"
      }
    }
    ```

    **On the sample:** **FAIL** · 10% unexpected (1 row) · unexpected values `["2026/09/28 06:00"]`

    The last row was written as `2026/09/28 06:00`.

## Table shape

Whole-table expectations.

| Check | Type | What it checks | Dimension | Parameters | Thresholds | Runs on |
|---|---|---|---|---|---|---|
| **Table row count in range** | `expect_table_row_count_to_be_between` | The table’s row count falls within [min, max]. | Completeness | `min_value` *(optional)*, `max_value` *(optional)* | warn / fail / critical | All datasources · SQL pushdown on Unity Catalog |

### Examples

??? example "Table row count in range"

    ```json
    {
      "name": "Table row count in range",
      "expectation_type": "expect_table_row_count_to_be_between",
      "config": {
        "min_value": 5,
        "max_value": 1000
      }
    }
    ```

    **On the sample:** **PASS** · observed `10`

    Ten rows is inside the band.

## Freshness

How stale is the target? Measured from a timestamp column (or file arrival time on flat files), reported in hours, banded by age. Requires a fail or critical threshold.

| Check | Type | What it checks | Dimension | Parameters | Thresholds | Runs on |
|---|---|---|---|---|---|---|
| **Freshness** | `monitor:freshness` | How stale is the target? Measures hours since the latest timestamp in the data — or, on a flat file with no column set, since the file last landed. | Timeliness | `column` | warn / fail / critical (fail or critical required) | All datasources |

### Examples

??? example "Freshness"

    ```json
    {
      "name": "Freshness",
      "expectation_type": "monitor:freshness",
      "kind": "freshness",
      "config": {
        "column": "shipped_at"
      },
      "warn_threshold": 24,
      "fail_threshold": 48
    }
    ```

    Reports the newest `shipped_at` and its age in hours as the metric; with these thresholds, 24 h old or more warns and 48 h fails. Leave `column` out on a flat file to measure the file's arrival time instead.

## Volume

Did the load deliver the expected row count? Banded by count. Requires a fail or critical threshold.

| Check | Type | What it checks | Dimension | Parameters | Thresholds | Runs on |
|---|---|---|---|---|---|---|
| **Volume** | `monitor:volume` | Did the load deliver the expected row count? Flags a count outside an allowed range. | Completeness | `min_rows`, `max_rows` | warn / fail / critical | All datasources |

### Examples

??? example "Volume"

    ```json
    {
      "name": "Volume",
      "expectation_type": "monitor:volume",
      "kind": "volume",
      "config": {
        "min_rows": 5,
        "max_rows": 1000
      }
    }
    ```

    Reports the row count as the metric; outside the band fails. On the sample, 10 rows passes.

## Aggregate

Does a column statistic — mean, median, sum, standard deviation, min or max — stay inside two-sided bands? The value is recorded every run, so it trends; an empty table or all-NULL column reports error, never a made-up 0.

| Check | Type | What it checks | Dimension | Parameters | Thresholds | Runs on |
|---|---|---|---|---|---|---|
| **Aggregate statistic** | `monitor:aggregate` | A column statistic — mean, median, sum, standard deviation, min or max — stays inside two-sided bands. The value itself is recorded every run, so it trends. An empty table or an all-NULL column reports error, never a made-up 0. | — (set it yourself) | `aggregate`, `column`, `min_value` *(optional)*, `max_value` *(optional)*, `warn_min` *(optional)*, `warn_max` *(optional)*, `critical_min` *(optional)*, `critical_max` *(optional)* | Two-sided warn / fail / critical bounds, set as parameters | All datasources — median not on MySQL / MariaDB, Trino, Amazon Athena (no exact median; refused at author time) |

### Examples

??? example "Aggregate statistic"

    ```json
    {
      "name": "Aggregate statistic",
      "expectation_type": "monitor:aggregate",
      "kind": "aggregate",
      "config": {
        "aggregate": "mean",
        "column": "amount",
        "min_value": 50,
        "warn_max": 100
      }
    }
    ```

    Reports the mean of `amount` as the metric and records it every run, so it trends. On the sample the mean is 105.87: above `warn_max`, inside the fail band, so WARN.

## Schema

Did the table's columns change against a captured baseline?

| Check | Type | What it checks | Dimension | Parameters | Thresholds | Runs on |
|---|---|---|---|---|---|---|
| **Schema drift** | `monitor:schema_drift` | Did the table’s column shape change? Diffs the live columns (names + types) against a baseline captured on the first run. Works on every datasource — warehouses via information_schema, flat files via the file header/footer, Iceberg from table metadata. | Consistency | `ignore_columns` *(optional)* | warn / fail / critical | All datasources |

### Examples

??? example "Schema drift"

    ```json
    {
      "name": "Schema drift",
      "expectation_type": "monitor:schema_drift",
      "kind": "schema_drift",
      "config": {
        "ignore_columns": [
          "legacy_code"
        ]
      }
    }
    ```

    The first run captures the columns and their types as the baseline; later runs report every added, dropped or retyped column. `legacy_code` is never compared.

## Anomaly

Is today's value unusual against a rolling baseline of this check's own history? Skips until enough history exists.

| Check | Type | What it checks | Dimension | Parameters | Thresholds | Runs on |
|---|---|---|---|---|---|---|
| **Anomaly** | `monitor:anomaly` | Learns a rolling baseline (mean/stddev) from this check’s own metric history and flags how far this run deviates (a z-score). Reports skip, never a fake pass/fail, until enough history accrues. | — (set it yourself) | `target_metric`, `column`, `window` *(optional)*, `min_points` *(optional)*, `seasonality` *(optional)* | warn / fail / critical (fail or critical required) | Snowflake, Unity Catalog, PostgreSQL, MySQL / MariaDB, Trino, SQL Server, Amazon Athena, Amazon Redshift |

### Examples

??? example "Anomaly"

    ```json
    {
      "name": "Anomaly",
      "expectation_type": "monitor:anomaly",
      "kind": "anomaly",
      "config": {
        "target_metric": "row_count",
        "window": 14,
        "min_points": 7,
        "seasonality": true
      },
      "warn_threshold": 2,
      "fail_threshold": 3
    }
    ```

    Reports the z-score of today's row count against the last 14 runs on the same weekday (`seasonality` keeps only those; off, it is the last 14 runs of any day). It skips until 7 such runs exist, so on a daily schedule about seven weeks.

## Comparison

Reconcile the suite's target against a second dataset, possibly on another connection.

| Check | Type | What it checks | Dimension | Parameters | Thresholds | Runs on |
|---|---|---|---|---|---|---|
| **Records reconciliation** | `comparison:records` | Diff this suite’s dataset (the target under test) against a baseline on another connection, joined on key columns — matched / mismatched / additional-per-side ROW buckets. | Consistency | — | warn / fail / critical | All datasources |
| **Column-level reconciliation** | `comparison:columns` | Same key-joined diff, counted per VALUE: each column reports its own matched / mismatched / additional-per-side counts. Pick this when you need to know WHICH columns drift, not just which rows. | Consistency | — | warn / fail / critical | All datasources |

### Examples

??? example "Records reconciliation"

    ```json
    {
      "name": "Records reconciliation",
      "expectation_type": "comparison:records",
      "kind": "comparison",
      "config": {
        "source": {
          "table": "orders_staging",
          "schema": "public"
        },
        "keys": [
          "order_id"
        ]
      },
      "source_connection_id": "<connection id>"
    }
    ```

    Joins the suite's target to `orders_staging` on `order_id` and reports matched, mismatched and missing rows on each side; set `source_connection_id` to the connection holding the source.

??? example "Column-level reconciliation"

    ```json
    {
      "name": "Column-level reconciliation",
      "expectation_type": "comparison:columns",
      "kind": "comparison",
      "config": {
        "source": {
          "table": "orders_staging",
          "schema": "public"
        },
        "keys": [
          "order_id"
        ],
        "columns": [
          "amount",
          "status"
        ]
      },
      "source_connection_id": "<connection id>"
    }
    ```

    Like the record comparison, but compares only the listed columns and reports the mismatch rate for each.

## Custom SQL

Any predicate you can write in SQL, validated before it runs.

| Check | Type | What it checks | Dimension | Parameters | Thresholds | Runs on |
|---|---|---|---|---|---|---|
| **Custom SQL** | `unexpected_rows_expectation` | A SQL query that should return no rows — any rows it returns are failures. | — (set it yourself) | `unexpected_rows_query` | warn / fail / critical | Snowflake, Unity Catalog, PostgreSQL, MySQL / MariaDB, Trino, SQL Server, Amazon Athena, Amazon Redshift |

### Examples

??? example "Custom SQL"

    ```json
    {
      "name": "Custom SQL",
      "expectation_type": "unexpected_rows_expectation",
      "config": {
        "unexpected_rows_query": "SELECT * FROM {batch} WHERE discount > amount"
      }
    }
    ```

    Every row the query returns is unexpected. On the sample, order 7's discount (150) exceeds its amount, so 1 row fails. `{batch}` is the suite's target.

## Snowflake DMF

Snowflake's native Data Metric Functions, evaluated inside Snowflake.

| Check | Type | What it checks | Dimension | Parameters | Thresholds | Runs on |
|---|---|---|---|---|---|---|
| **Null count (DMF)** | `dmf:null_count` | Snowflake’s system NULL_COUNT metric function, computed natively in the warehouse. | Completeness | `column` | warn / fail / critical (fail or critical required) | Snowflake |
| **Null percent (DMF)** | `dmf:null_percent` | Snowflake’s system NULL_PERCENT metric function (0–100), computed natively in the warehouse. | Completeness | `column` | warn / fail / critical (fail or critical required) | Snowflake |
| **Duplicate count (DMF)** | `dmf:duplicate_count` | Snowflake’s system DUPLICATE_COUNT metric function, computed natively in the warehouse. | Uniqueness | `column` | warn / fail / critical (fail or critical required) | Snowflake |
| **Blank count (DMF)** | `dmf:blank_count` | Snowflake’s system BLANK_COUNT metric function, computed natively in the warehouse — counts empty or space-only strings (not NULLs; tabs/newlines aren’t treated as blank). VARCHAR columns only. | Completeness | `column` | warn / fail / critical (fail or critical required) | Snowflake |
| **Future timestamp percent (DMF)** | `dmf:future_timestamp_percent` | Snowflake’s system FUTURE_TIMESTAMP_PERCENT metric function (0–100): the share of rows dated after the evaluation time. DATE, TIMESTAMP_LTZ and TIMESTAMP_TZ columns only. | Validity | `column` | warn / fail / critical (fail or critical required) | Snowflake |
| **Accepted values (DMF)** | `dmf:accepted_values` | Snowflake’s system ACCEPTED_VALUES metric function, evaluated in the warehouse: counts rows whose value is not in the list. NULLs are not counted as violations. | Validity | `column`, `value_set` | warn / fail / critical (fail or critical required) | Snowflake |
| **Unique count (DMF)** | `dmf:unique_count` | Snowflake’s system UNIQUE_COUNT metric function, computed natively in the warehouse. Degrades downward, so this type carries no thresholds — read the observed value directly. | Uniqueness | `column` | None — pass/fail only | Snowflake |
| **Custom DMF** | `dmf:custom` | A data metric function your team created in Snowflake (CREATE DATA METRIC FUNCTION), called in the warehouse over columns of this suite’s table. Its return value is the metric, shown as-is — write DMFs that return a count or a percentage, not a data value. | — (set it yourself) | `function`, `columns` | warn / fail / critical (fail or critical required) | Snowflake |

### Examples

??? example "Null count (DMF)"

    ```json
    {
      "name": "Null count (DMF)",
      "expectation_type": "dmf:null_count",
      "engine": "dmf",
      "config": {
        "column": "CUSTOMER_EMAIL"
      },
      "fail_threshold": 1
    }
    ```

    Snowflake counts the NULLs itself (`SNOWFLAKE.CORE.NULL_COUNT`); the count is the metric. On the sample it would be 1.

??? example "Null percent (DMF)"

    ```json
    {
      "name": "Null percent (DMF)",
      "expectation_type": "dmf:null_percent",
      "engine": "dmf",
      "config": {
        "column": "CUSTOMER_EMAIL"
      },
      "warn_threshold": 1,
      "fail_threshold": 5
    }
    ```

    The NULL percentage (10 on the sample).

??? example "Duplicate count (DMF)"

    ```json
    {
      "name": "Duplicate count (DMF)",
      "expectation_type": "dmf:duplicate_count",
      "engine": "dmf",
      "config": {
        "column": "ORDER_ID"
      },
      "fail_threshold": 1
    }
    ```

    Rows whose value repeats another row's (2 on the sample: both order 7s).

??? example "Blank count (DMF)"

    ```json
    {
      "name": "Blank count (DMF)",
      "expectation_type": "dmf:blank_count",
      "engine": "dmf",
      "config": {
        "column": "STATUS"
      },
      "fail_threshold": 1
    }
    ```

    Empty or whitespace-only strings; a NULL is not blank.

??? example "Future timestamp percent (DMF)"

    ```json
    {
      "name": "Future timestamp percent (DMF)",
      "expectation_type": "dmf:future_timestamp_percent",
      "engine": "dmf",
      "config": {
        "column": "SHIPPED_AT"
      },
      "fail_threshold": 0.01
    }
    ```

    The percentage of timestamps later than now; takes DATE, TIMESTAMP_LTZ and TIMESTAMP_TZ columns only.

??? example "Accepted values (DMF)"

    ```json
    {
      "name": "Accepted values (DMF)",
      "expectation_type": "dmf:accepted_values",
      "engine": "dmf",
      "config": {
        "column": "STATUS",
        "value_set": [
          "pending",
          "shipped",
          "cancelled"
        ]
      },
      "fail_threshold": 1
    }
    ```

    Values outside the set.

??? example "Unique count (DMF)"

    ```json
    {
      "name": "Unique count (DMF)",
      "expectation_type": "dmf:unique_count",
      "engine": "dmf",
      "config": {
        "column": "ORDER_ID"
      }
    }
    ```

    The number of distinct non-NULL values (9 on the sample).

??? example "Custom DMF"

    ```json
    {
      "name": "Custom DMF",
      "expectation_type": "dmf:custom",
      "engine": "dmf",
      "config": {
        "function": "ANALYTICS.DQ.NEGATIVE_AMOUNTS",
        "columns": [
          "AMOUNT"
        ]
      },
      "fail_threshold": 1
    }
    ```

    Calls your own data metric function over the listed columns; whatever it returns is the metric, banded by the thresholds.

## Databricks DQX

Databricks Labs DQX row rules, evaluated by a serverless job in your own workspace.

| Check | Type | What it checks | Dimension | Parameters | Thresholds | Runs on |
|---|---|---|---|---|---|---|
| **Not null (DQX)** | `dqx:is_not_null` | Rows where the column is NULL. | Completeness | `column`, `mode` *(optional)* | warn / fail / critical | All datasources |
| **Not empty (DQX)** | `dqx:is_not_empty` | Rows where the column is an empty string. | Completeness | `column`, `mode` *(optional)* | warn / fail / critical | All datasources |
| **Not null or empty (DQX)** | `dqx:is_not_null_and_not_empty` | Rows where the column is NULL or an empty string. | Completeness | `column`, `mode` *(optional)* | warn / fail / critical | All datasources |
| **In list (DQX)** | `dqx:is_in_list` | Rows whose value is not one of the allowed values. | Validity | `column`, `allowed`, `mode` *(optional)* | warn / fail / critical | All datasources |
| **In range (DQX)** | `dqx:is_in_range` | Rows whose value falls outside the inclusive range. | Validity | `column`, `min_limit`, `max_limit`, `mode` *(optional)* | warn / fail / critical | All datasources |
| **Matches regex (DQX)** | `dqx:regex_match` | Rows whose value does not match the regular expression. | Validity | `column`, `regex`, `mode` *(optional)* | warn / fail / critical | All datasources |
| **Not less than (DQX)** | `dqx:is_not_less_than` | Rows whose value is below the limit. | Validity | `column`, `limit`, `mode` *(optional)* | warn / fail / critical | All datasources |
| **Not greater than (DQX)** | `dqx:is_not_greater_than` | Rows whose value is above the limit. | Validity | `column`, `limit`, `mode` *(optional)* | warn / fail / critical | All datasources |

### Examples

??? example "Not null (DQX)"

    ```json
    {
      "name": "Not null (DQX)",
      "expectation_type": "dqx:is_not_null",
      "engine": "dqx",
      "config": {
        "column": "customer_email"
      },
      "fail_threshold": 1
    }
    ```

    Databricks DQX counts failing rows in your workspace; the failing count is the metric (1 on the sample).

??? example "Not empty (DQX)"

    ```json
    {
      "name": "Not empty (DQX)",
      "expectation_type": "dqx:is_not_empty",
      "engine": "dqx",
      "config": {
        "column": "status"
      },
      "fail_threshold": 1
    }
    ```

    Rows whose value is an empty string.

??? example "Not null or empty (DQX)"

    ```json
    {
      "name": "Not null or empty (DQX)",
      "expectation_type": "dqx:is_not_null_and_not_empty",
      "engine": "dqx",
      "config": {
        "column": "status",
        "mode": "stream"
      },
      "fail_threshold": 1
    }
    ```

    NULL or empty. `mode: stream` evaluates only rows appended since the last run.

??? example "In list (DQX)"

    ```json
    {
      "name": "In list (DQX)",
      "expectation_type": "dqx:is_in_list",
      "engine": "dqx",
      "config": {
        "column": "status",
        "allowed": [
          "pending",
          "shipped",
          "cancelled"
        ]
      },
      "fail_threshold": 1
    }
    ```

    Rows outside the list (`refunded` on the sample).

??? example "In range (DQX)"

    ```json
    {
      "name": "In range (DQX)",
      "expectation_type": "dqx:is_in_range",
      "engine": "dqx",
      "config": {
        "column": "amount",
        "min_limit": 0,
        "max_limit": 1000
      },
      "fail_threshold": 1
    }
    ```

    Rows outside [0, 1000] (order 6).

??? example "Matches regex (DQX)"

    ```json
    {
      "name": "Matches regex (DQX)",
      "expectation_type": "dqx:regex_match",
      "engine": "dqx",
      "config": {
        "column": "sku",
        "regex": "^SKU-[0-9]{4}$"
      },
      "fail_threshold": 1
    }
    ```

    Rows not matching (`sku_8`).

??? example "Not less than (DQX)"

    ```json
    {
      "name": "Not less than (DQX)",
      "expectation_type": "dqx:is_not_less_than",
      "engine": "dqx",
      "config": {
        "column": "amount",
        "limit": 0
      },
      "fail_threshold": 1
    }
    ```

    Rows below the limit.

??? example "Not greater than (DQX)"

    ```json
    {
      "name": "Not greater than (DQX)",
      "expectation_type": "dqx:is_not_greater_than",
      "engine": "dqx",
      "config": {
        "column": "discount",
        "limit": 100
      },
      "fail_threshold": 1
    }
    ```

    Rows above the limit (order 7's discount of 150).

## Authorable outside the editor

Vetted by the backend but with no editor widget: usable over the REST API, MCP and
suite import, which hand the backend raw JSON.

- `expect_column_pair_values_to_be_in_set`

## Not offered, and why

**GX's scalar aggregates** (`expect_column_mean_to_be_between` and its siblings) report one
number and no unexpected-%, so severity bands have nothing to band — the Aggregate monitor
measures that shape instead, with two-sided bands and a trend. **Whole-table column-set
comparisons** are what the Schema-drift monitor does against a captured baseline. For
anything else, write a custom-SQL check.

---

*Generated by `scripts/docs/gen-check-catalog.py` — edit the catalog or the allowlist, not this page.*
