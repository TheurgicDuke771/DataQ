# Datasources & checks

## Supported datasources

| Datasource | Connection | Check authoring | Execution |
|---|---|---|---|
| Snowflake (DEV/QA/UAT) | account + user + key/PAT | ✅ | ✅ |
| ADLS Gen2 (flat files) | account URL + container, SAS | ✅ | ✅ |
| AWS S3 (flat files) | bucket + region, access key | ✅ | ✅ |
| Unity Catalog (Databricks) | workspace URL + warehouse + PAT | ✅ | ✅ |
| Apache Iceberg | catalog URI + catalog type (REST/SQL/Glue/Hive) + optional storage credential | ✅ | ✅ |

## Add a connection

In the UI, **Connections → Add connection**, pick the datasource, fill the type-specific
fields, and **Test** it (a live reachability probe). Credentials are stored in the secret
store (Azure Key Vault in production), never in the database.

Snowflake supports two auth modes: **password** and **key pair (RSA)**. For key pair,
paste the PEM private key; if the key is passphrase-protected (PKCS#8), fill the optional
**Key passphrase** field — both parts are stored together as one secret and rotate
atomically via **Re-auth**. Leave the passphrase blank for an unencrypted key.
Key-pair connections also require **Role** (the GX key-pair form mandates one for suite
runs, so it is validated when the connection is saved).

### Identifier casing (Snowflake / Unity Catalog)

Warehouses fold **unquoted** identifiers — Snowflake upper-cases them — so a column
created as `order_ts` is really stored as `ORDER_TS`, while one created as
`"Amount"` is stored mixed-case and is only reachable quoted.

DataQ handles both: type the name **exactly as the column dropdown reports it**.
Lower-case names are sent unquoted and fold as the warehouse always folded them;
anything else is quoted for you, using the right quote character for the engine
(Snowflake `"`, Databricks backticks). This applies to every SQL path — profiling,
the aggregate/top-values queries, and freshness/volume monitors alike.

**Still unsupported:** identifiers that need quoting for reasons other than case —
spaces, dots, leading digits, or non-ASCII characters. These are refused with a
422 rather than silently mis-resolved. If you have such a column, alias it in a
view and point the check at that.

### Flat files: formats and CSV delimiters

Flat-file connections (ADLS Gen2 / S3) read `.csv` and `.parquet`/`.pq`. **The CSV
delimiter is detected per file, not per connection** — a connection is a whole
bucket/container and the files under it need not agree, so DataQ sniffs each file's
header. Comma, semicolon, tab, and pipe are recognised; anything it can't decide
(a single-column file, an empty file) is read as comma-separated.

If a file uses some other separator, DataQ will parse the whole header as one
column — the symptom is a **column dropdown offering a single long name** like
`id;email;amount`. Convert the file to one of the four separators, or to Parquet.

## Author a check

1. Create (or open) a **suite** and point it at a **target** — a table (Snowflake/UC), a
   file/path or batch pattern (ADLS/S3), or an Iceberg `namespace.table`.
2. **Add check** opens a dedicated page (`/suites/<id>/checks/new`): pick a **category**,
   then the check type, then fill its config. The four authoring paths:

### GX expectation (all datasources)

Pick a *Column values* / *Table shape* expectation (e.g. `Column values not null`), name
it, set the column, and optionally band severity with **Warn ≥ / Fail ≥ / Critical ≥**
thresholds over the unexpected-%. Leave thresholds blank for binary pass/fail.

### Custom SQL (Snowflake / Unity Catalog — ADR 0019)

A read-only SQL rule in the Monaco editor: **any rows returned are failures**. Use
`{batch}` as a placeholder for the suite's target table
(`SELECT * FROM {batch} WHERE amount < 0`). Single read-only statement enforced
server-side.

### Freshness monitor (SQL datasources + Iceberg — ADR 0012/0030)

*How stale is the table?* Point it at the load/updated **timestamp column**; the check
measures hours since `MAX(column)` and bands that age with the thresholds. A **fail or
critical threshold is required** — without one, a freshness check could never fail.

### Volume monitor (SQL datasources + Iceberg — ADR 0012/0030)

*Did the load deliver?* Set the expected **min/max row count**; thresholds optionally
band the % by which the count falls outside the range (a spike can exceed 100%), or
leave them blank for binary in-range pass/fail.

### Type names for `expect_column_values_to_be_of_type`

The **Column values are of type** expectation's `type_` field is the one place the
check editor's "obvious" answer is usually wrong. GX validates it against a
*different* type vocabulary depending on which engine actually runs the check — not
the type your warehouse/catalog shows you:

- **Snowflake** builds a real SQL batch (`SqlAlchemyExecutionEngine`) and string-compares
  `type_` against the **fully-qualified dialect type**, not the short column type. A
  `NUMBER` column reports as `DECIMAL(38, 0)`; `VARCHAR` reports as `VARCHAR(16777216)`.
  Plugging in `NUMBER` or `DECIMAL` alone fails every time.
- **Unity Catalog, ADLS Gen2 / S3, and Apache Iceberg** all read the target into a
  pandas DataFrame first (`PandasExecutionEngine`). GX first tries an **exact dtype
  match**; only when the column's dtype is `object` and `type_` isn't
  `object`/`object_`/`O` does it fall back to a **row-wise Python value-type compare**.
  In practice:
  - Numeric columns report numpy dtypes — `int64`, `float64`, `bool`. **Caveat:** an
    integer column containing *any* NULL is upcast to `float64` by the read
    (`pd.read_sql_table` / `pd.read_csv`), so a nullable `BIGINT` reports `float64`,
    not `int64`.
  - String columns on **Unity Catalog and CSV** reads are plain pandas `object` dtype
    (these reads are *not* Arrow-backed). Both `type_: object` (exact dtype match) and
    `type_: str` (row-wise value-type match) pass — pick either.
  - **Parquet and Iceberg** reads *are* Arrow-backed and can report Arrow-flavored
    dtype names — calibrate from a dry-run rather than assuming the CSV/UC names.

| Datasource | Engine | `type_` guidance |
|---|---|---|
| Snowflake | SQL (dialect-native) | `DECIMAL(38, 0)` for `NUMBER`, `VARCHAR(16777216)` for `VARCHAR` |
| Unity Catalog | pandas DataFrame (not Arrow-backed) | `int64` for non-nullable `BIGINT` (**`float64` if the column contains NULLs**); `object` or `str` for `STRING` |
| ADLS Gen2 / S3 (CSV) | pandas DataFrame (not Arrow-backed) | `int64`/`float64`/`bool` for numerics (**NULLs upcast integers to `float64`**); `object` or `str` for strings |
| ADLS Gen2 / S3 (Parquet) / Iceberg | pandas DataFrame (Arrow-backed) | Arrow-flavored dtype names — confirm via a dry-run's `observed_value` |

**Calibration tip:** don't guess — **dry-run first**, but know where the trail runs
out. On **Snowflake and the Arrow-backed sources** (Parquet/Iceberg), a failing
result's `observed_value` carries the *exact* string GX expected — copy it into
`type_` and re-run to confirm green. On **Unity Catalog / CSV**, a wrong value-type
guess (e.g. `int64` against a string column) falls to GX's row-wise compare, which
fails with **no observed value at all** — the dry-run preview renders Observed as
"—". If you see that, don't hunt for a magic string: the column is `object` dtype, so
enter `object` or the Python value type name (`str`). The check editor's help text
under the field repeats this per the suite's connection type.

Before saving any of them: **Dry-run** previews pass/fail against live data, and the
**column profiler** (nulls, distinct count, min/max, top values) helps place thresholds.

## Run it

Run a suite **now**, on a **[cron schedule](scheduling.md)**, or **triggered** by a
pipeline (see **[Orchestration](orchestration.md)**). Results land on the **Results**
page and the **Dashboard** (health score + trends); failures alert per the suite's
**[notification config](notifications.md)**.

Severity comes from thresholds banding the observed unexpected-percentage
(warn < fail < critical); see ADR 0005 / 0016 for the model, and
**[Best practices](best-practices.md)** for how to pick the bands.
