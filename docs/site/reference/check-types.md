# Check types

Every kind of check DataQ can author, generated from the check editor's catalog and the
backend's vetted allowlist — so this page cannot drift from what the product actually
offers. Every GX type on this page is executed in CI on a dataframe batch, and on a SQL batch too unless its row says it is dataframe-only.

| | Count |
|---|---|
| Check types in the editor | 35 |
| GX expectation types vetted by the backend | 25 |

How to read a row: **Parameters** are the editor's fields (`mostly` is GX's optional row
tolerance, a fraction). **Thresholds** are the severity bands read from the result.
**Dimension** is the default data-quality dimension the check is classified under; you can
change it on any check.

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
| **Column values match regex** | `expect_column_values_to_match_regex` | Every value matches the given regular expression. | Validity | `column`, `regex`, `mostly` *(optional)* | warn / fail / critical | All datasources · SQL pushdown on Unity Catalog |
| **Column values do not match regex** | `expect_column_values_to_not_match_regex` | No value matches the given regular expression — for catching a pattern that should never appear (a stray delimiter, an unredacted identifier). | Validity | `column`, `regex`, `mostly` *(optional)* | warn / fail / critical | All datasources · SQL pushdown on Unity Catalog |
| **Column values match a list of regexes** | `expect_column_values_to_match_regex_list` | Every value matches the regexes in the list — by default ANY one of them is enough, for a column carrying several legitimate formats (e.g. two phone-number conventions). | Validity | `column`, `regex_list`, `match_on` *(optional)*, `mostly` *(optional)* | warn / fail / critical | All datasources · SQL pushdown on Unity Catalog |
| **Column values match none of a list of regexes** | `expect_column_values_to_not_match_regex_list` | No value matches ANY regex in the list — a deny-list of forbidden formats. | Validity | `column`, `regex_list`, `mostly` *(optional)* | warn / fail / critical | All datasources · SQL pushdown on Unity Catalog |
| **Column values are valid JSON** | `expect_column_values_to_be_json_parseable` | Every value parses as JSON — for a payload/metadata column stored as text. Not offered on Snowflake: Great Expectations implements this one only for dataframe batches, so a SQL warehouse would error on every run. Use a custom-SQL check (or a VARIANT column) there. | Validity | `column`, `mostly` *(optional)* | warn / fail / critical | ADLS Gen2, AWS S3, Unity Catalog, Apache Iceberg — not Snowflake (no SQL implementation; refused at author time) |
| **Column values are of type** | `expect_column_values_to_be_of_type` | Every value in the column matches the given data type. | Validity | `column`, `type_`, `mostly` *(optional)* | warn / fail / critical | All datasources |
| **Column values are of one of several types** | `expect_column_values_to_be_in_type_list` | Every value in the column matches at least one of the given data types — the tolerant sibling of “Column values are of type”, for a column whose type legitimately varies by datasource or load. | Validity | `column`, `type_list`, `mostly` *(optional)* | warn / fail / critical | All datasources |
| **Compound columns unique** | `expect_compound_columns_to_be_unique` | The COMBINATION of values across the listed columns is distinct on every row — a multi-column primary or business key. Each column on its own may repeat freely. | Uniqueness | `column_list`, `mostly` *(optional)* | warn / fail / critical | All datasources · SQL pushdown on Unity Catalog |
| **Column A greater than column B** | `expect_column_pair_values_a_to_be_greater_than_b` | Row by row, column A is greater than column B — e.g. ended_at > started_at, or total >= discount. | Validity | `column_A`, `column_B`, `or_equal` *(optional)*, `mostly` *(optional)* | warn / fail / critical | All datasources · SQL pushdown on Unity Catalog |
| **Column A equals column B** | `expect_column_pair_values_to_be_equal` | Row by row, the two columns hold the same value — e.g. a denormalised copy that must agree with its source, or a total that must match a recomputed one. | Validity | `column_A`, `column_B`, `mostly` *(optional)* | warn / fail / critical | All datasources · SQL pushdown on Unity Catalog |
| **Values unique within each row** | `expect_select_column_values_to_be_unique_within_record` | Within a single row, the listed columns all hold different values — e.g. a transfer whose source and destination account must not be the same. This is per-row; use “Compound columns unique” for uniqueness ACROSS rows. | Uniqueness | `column_list`, `mostly` *(optional)* | warn / fail / critical | All datasources · SQL pushdown on Unity Catalog |
| **Columns sum to a total** | `expect_multicolumn_sum_to_equal` | Row by row, the listed columns add up to the given total — e.g. subtotal + tax + shipping = total. | Validity | `column_list`, `sum_total`, `mostly` *(optional)* | warn / fail / critical | All datasources · SQL pushdown on Unity Catalog |
| **Column distinct values in set** | `expect_column_distinct_values_to_be_in_set` | Every DISTINCT value present in the column is one of an allowed set — reports WHICH unexpected values exist rather than how many rows carry them. Use “Column values in set” when you care about the row count. | Validity | `column`, `value_set` | None — pass/fail only | All datasources · SQL pushdown on Unity Catalog |
| **Column distinct values contain set** | `expect_column_distinct_values_to_contain_set` | Every value in the given set appears at least once in the column — catches a category that stopped arriving. The column may also contain other values. | Completeness | `column`, `value_set` | None — pass/fail only | All datasources · SQL pushdown on Unity Catalog |
| **Column values match a date format** | `expect_column_values_to_match_strftime_format` | Every value parses under the given strftime format — for a date or timestamp stored as text. Not offered on Snowflake: Great Expectations implements this one only for dataframe batches, so a SQL warehouse would error on every run. Use a custom-SQL check there. | Validity | `column`, `strftime_format`, `mostly` *(optional)* | warn / fail / critical | ADLS Gen2, AWS S3, Unity Catalog, Apache Iceberg — not Snowflake (no SQL implementation; refused at author time) |

## Table shape

Whole-table expectations.

| Check | Type | What it checks | Dimension | Parameters | Thresholds | Runs on |
|---|---|---|---|---|---|---|
| **Table row count in range** | `expect_table_row_count_to_be_between` | The table’s row count falls within [min, max]. | Completeness | `min_value` *(optional)*, `max_value` *(optional)* | warn / fail / critical | All datasources · SQL pushdown on Unity Catalog |

## Freshness

How stale is the target? Measured from a timestamp column (or file arrival time on flat files), reported in hours, banded by age. Requires a fail or critical threshold.

| Check | Type | What it checks | Dimension | Parameters | Thresholds | Runs on |
|---|---|---|---|---|---|---|
| **Freshness** | `monitor:freshness` | How stale is the target? Measures hours since the latest timestamp in the data — or, on a flat file with no column set, since the file last landed. | Timeliness | `column` | warn / fail / critical (fail or critical required) | All datasources |

## Volume

Did the load deliver the expected row count? Banded by count. Requires a fail or critical threshold.

| Check | Type | What it checks | Dimension | Parameters | Thresholds | Runs on |
|---|---|---|---|---|---|---|
| **Volume** | `monitor:volume` | Did the load deliver the expected row count? Flags a count outside an allowed range. | Completeness | `min_rows`, `max_rows` | warn / fail / critical | All datasources |

## Schema

Did the table's columns change against a captured baseline?

| Check | Type | What it checks | Dimension | Parameters | Thresholds | Runs on |
|---|---|---|---|---|---|---|
| **Schema drift** | `monitor:schema_drift` | Did the table’s column shape change? Diffs the live columns (names + types) against a baseline captured on the first run. Works on every datasource — warehouses via information_schema, flat files via the file header/footer, Iceberg from table metadata. | Consistency | `ignore_columns` *(optional)* | warn / fail / critical | All datasources |

## Anomaly

Is today's value unusual against a rolling baseline of this check's own history? Skips until enough history exists.

| Check | Type | What it checks | Dimension | Parameters | Thresholds | Runs on |
|---|---|---|---|---|---|---|
| **Anomaly** | `monitor:anomaly` | Learns a rolling baseline (mean/stddev) from this check’s own metric history and flags how far this run deviates (a z-score). Reports skip, never a fake pass/fail, until enough history accrues. | — (set it yourself) | `target_metric`, `column`, `window` *(optional)*, `min_points` *(optional)*, `seasonality` *(optional)* | warn / fail / critical (fail or critical required) | Snowflake, Unity Catalog |

## Comparison

Reconcile the suite's target against a second dataset, possibly on another connection.

| Check | Type | What it checks | Dimension | Parameters | Thresholds | Runs on |
|---|---|---|---|---|---|---|
| **Records reconciliation** | `comparison:records` | Diff this suite’s dataset (the target under test) against a baseline on another connection, joined on key columns — matched / mismatched / additional-per-side ROW buckets. | Consistency | — | warn / fail / critical | All datasources |
| **Column-level reconciliation** | `comparison:columns` | Same key-joined diff, counted per VALUE: each column reports its own matched / mismatched / additional-per-side counts. Pick this when you need to know WHICH columns drift, not just which rows. | Consistency | — | warn / fail / critical | All datasources |

## Custom SQL

Any predicate you can write in SQL, validated before it runs.

| Check | Type | What it checks | Dimension | Parameters | Thresholds | Runs on |
|---|---|---|---|---|---|---|
| **Custom SQL** | `unexpected_rows_expectation` | A SQL query that should return no rows — any rows it returns are failures. | — (set it yourself) | `unexpected_rows_query` | warn / fail / critical | Snowflake, Unity Catalog |

## Snowflake DMF

Snowflake's native Data Metric Functions, evaluated inside Snowflake.

| Check | Type | What it checks | Dimension | Parameters | Thresholds | Runs on |
|---|---|---|---|---|---|---|
| **Null count (DMF)** | `dmf:null_count` | Snowflake’s system NULL_COUNT metric function, computed natively in the warehouse. | Completeness | `column` | warn / fail / critical (fail or critical required) | Snowflake |
| **Null percent (DMF)** | `dmf:null_percent` | Snowflake’s system NULL_PERCENT metric function (0–100), computed natively in the warehouse. | Completeness | `column` | warn / fail / critical (fail or critical required) | Snowflake |
| **Duplicate count (DMF)** | `dmf:duplicate_count` | Snowflake’s system DUPLICATE_COUNT metric function, computed natively in the warehouse. | Uniqueness | `column` | warn / fail / critical (fail or critical required) | Snowflake |
| **Unique count (DMF)** | `dmf:unique_count` | Snowflake’s system UNIQUE_COUNT metric function, computed natively in the warehouse. Degrades downward, so this type carries no thresholds — read the observed value directly. | Uniqueness | `column` | None — pass/fail only | Snowflake |

## Authorable outside the editor

Vetted by the backend but with no editor widget: usable over the REST API, MCP and
suite import, which hand the backend raw JSON.

- `expect_column_pair_values_to_be_in_set`

## Not offered, and why

**Scalar aggregates** (`expect_column_mean_to_be_between` and its siblings) report one number
and no unexpected-%, so severity bands have nothing to band — a Volume or Anomaly monitor
measures that shape with trends and a learned baseline. **Whole-table column-set
comparisons** are what the Schema-drift monitor does against a captured baseline. For
anything else, write a custom-SQL check.

---

*Generated by `scripts/docs/gen-check-catalog.py` — edit the catalog or the allowlist, not this page.*
