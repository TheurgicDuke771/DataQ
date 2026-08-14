# Datasources & checks

## Supported datasources

| Datasource | Connection | Check authoring | Execution |
|---|---|---|---|
| Snowflake (DEV/QA/UAT) | account + user + key/PAT | ✅ | ✅ |
| ADLS Gen2 (flat files) | account URL + container, SAS | ✅ | ✅ |
| AWS S3 **and S3-compatible** (flat files) | bucket + region, access key (+ optional endpoint) | ✅ | ✅ |
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

### S3-compatible object stores

The **S3** connection is not AWS-only. Leave **Endpoint URL** blank and it addresses AWS
exactly as before; set it to a store that speaks the S3 API — MinIO, Ceph/RadosGW,
Cloudflare R2, Wasabi, Backblaze B2, SeaweedFS, or an on-prem gateway — and the same
connection, checks and monitors work unchanged.

- **Endpoint URL** — the full base URL including scheme, e.g.
  `https://minio.example.com:9000`. A URL without `http://`/`https://` is rejected at
  save time rather than failing later as an opaque connection error.
- **Addressing style** — `auto` (default), `path` or `virtual`. `auto` uses **path**
  addressing whenever an endpoint is set, which is what MinIO and SeaweedFS require;
  with no endpoint it leaves AWS's default untouched. Override it only if your store
  needs the other form (R2 and Wasabi accept virtual-host addressing).
- **Region** is still required — S3-compatible stores generally ignore it, but the AWS
  SDK requires a value. `us-east-1` is the conventional filler.
- The endpoint must **not** embed a credential (`https://key:secret@host`). Connection
  `config` is stored and returned in plaintext, so a credential there would live outside
  the secret store; it is rejected at save time. Put the key in **Access key ID** and the
  secret in the credential field.
- An `http://` endpoint is accepted (in-cluster MinIO usually has no TLS), but object
  bytes and request signatures then travel **cleartext** — prefer `https://` for anything
  crossing a network you don't control.

**Asset identity.** With no **Endpoint URL** (AWS), the asset namespace is exactly
`s3://{bucket}` — unchanged, since AWS bucket names are globally unique and this form
is already persisted on every S3 asset in production. With an **Endpoint URL** set, the
namespace becomes `s3://{host[:port]}/{bucket}` — the endpoint's authority (scheme
stripped, default port elided, host lower-cased) joins the bucket, so two stores that
happen to share a bucket name (an AWS bucket and a MinIO bucket both named `landing`,
say) resolve to *different* assets instead of merging their scorecards, lineage and
incidents ([#1064](https://github.com/TheurgicDuke771/DataQ/issues/1064), decided in
[ADR 0040](adr/0040-warehouse-inventory-sync-table-enumeration-seam.md) §6).

The same two fields exist on a **dbt** orchestration connection whose `artifacts_uri` is
`s3://…`, so the artifacts poll can read from the same store.

### Identifier casing (Snowflake / Unity Catalog)

Warehouses fold **unquoted** identifiers — Snowflake upper-cases them — so a column
created as `order_ts` is really stored as `ORDER_TS`, while one created as
`"Amount"` is stored mixed-case and is only reachable quoted.

DataQ handles both: type the name **exactly as the column dropdown reports it**.
Lower-case names are sent unquoted and fold as the warehouse always folded them;
anything else is quoted for you, using the right quote character for the engine
(Snowflake `"`, Databricks backticks). This applies to every SQL path — profiling,
the aggregate/top-values queries, and freshness/volume monitors alike.

**Still unsupported**, and refused with a 422 rather than silently mis-resolved:

- Identifiers needing quotes for reasons other than case — spaces, dots, leading
  digits, non-ASCII characters.
- Genuinely **reserved words** (`order`, `select`, …) used as a column or table
  name. An unquoted `order` is stored `ORDER`, which neither `order` (parse error)
  nor `"order"` (wrong case) reaches.

In both cases, alias the column in a view and point the check at that.

One more caveat: in a **three-part** `catalog.schema.table` target, only the table
gets quoted — a mixed-case *catalog or schema* still folds
([#936](https://github.com/TheurgicDuke771/DataQ/issues/936)). This affects nobody
today (Unity Catalog is the only three-part datasource and it resolves identifiers
case-insensitively), but don't rely on it if that changes.

### Seeing coverage: the asset scorecard

Because every check carries a dimension, the **asset page** shows a *Data quality
by dimension* panel: per-dimension score and check counts, plus — the part worth
looking at — the dimensions with **no checks at all**.

Three states, deliberately kept distinct:

| What you see | What it means |
|---|---|
| A score bar | Checks exist and evaluated in the latest run. |
| **No signal** | Checks exist, but none evaluated — not yet run, or all skipped/errored. Not 0%: nothing was measured. |
| Listed under **Not covered** | No checks for that dimension exist at all. Not 0%, and *definitely* not 100%. |

**Coverage counts checks, not runs.** A check you author today counts as coverage
immediately — it does not need a completed run first, and a suite whose latest run
failed does not lose its coverage. The score is the part that waits for a run.

The `3/5 passing` figure counts checks that passed in the latest run out of checks
that exist, so the gap includes failing, skipped, errored **and** never-run checks;
hover it to see how many were excluded from the score.

The numbers are **workspace-wide**: everyone who can see the asset sees the same
score, whether or not they can open the suites behind it. Two people comparing
notes on the same table should never see two different verdicts.

Checks with no dimension set are counted separately ("N checks have no dimension
set") rather than filed under a dimension — otherwise "Not covered" would be wrong.

### Flat files: formats and CSV delimiters

Flat-file connections (ADLS Gen2 / S3) read `.csv` and `.parquet`/`.pq`. **The CSV
delimiter is detected per file, not per connection** — a connection is a whole
bucket/container and the files under it need not agree, so DataQ sniffs each file's
header. Comma, semicolon, tab, and pipe are recognised; anything it can't decide
(a single-column file, an empty file) is read as comma-separated.

If a file uses some other separator, DataQ will parse the whole header as one
column — the symptom is a **column dropdown offering a single long name** like
`id;email;amount`. Convert the file to one of the four separators, or to Parquet.

### Very large targets: sampling and the scan cap (#595)

Snowflake and Iceberg monitors answer from the warehouse or from file metadata, so
size is not a worker concern there. **Flat files and Unity Catalog are different**:
their expectations run against a DataFrame the worker holds, so a big enough target
runs the worker out of memory. Two things guard that.

**A hard cap, on by default.** Before a run materializes anything it checks the
object's size (flat files) or the table's `COUNT(*)` (Unity Catalog). Over the cap
the run ends **failed** with a message naming the target, the two numbers, and what
to do — never a half-finished run or a silent hang. Defaults are 128 MiB and 1.5M
rows, tuned for the reference 2 GiB worker; an operator can change them
(`RUN_MAX_SCAN_BYTES` / `RUN_MAX_SCAN_ROWS` — see `deploy/README.md`).

**Sampling, opt-in per suite.** Add a `sampling` block to the suite's target and
checks run against a bounded sample instead of the whole dataset:

```json
{ "path": "landing/orders.csv",
  "sampling": { "strategy": "head", "rows": 100000 } }
```

- `head` — the first N rows. Cheapest by far (a bounded read that stops early), but
  **not representative**: files usually arrive ordered by load time, so a head
  sample sees one slice of the key space. Good for a smoke check, not for a
  uniqueness claim.
- `random` — N rows drawn uniformly across the whole dataset. Representative;
  costs one extra cheap pass to learn the population size. Add `"seed": <int>` to
  make a run reproducible — leave it out and each run inspects different rows,
  which is usually what you want from a monitor.

Sampling **replaces** the size cap for that suite (the read is bounded by the
sample), so it is the supported way to check a target that is otherwise too big.

Things it deliberately **refuses** rather than silently allowing:

- **Sampling on Snowflake or Iceberg targets** — Snowflake never loads rows, so a
  sample there would change nothing while labelling every result "sampled".
- **Freshness monitors are never sampled** — a `MAX` over a sample is a *smaller*
  maximum, which would report healthy data as critically stale.
- **Table row-count expectations on a sampled suite** (`expect_table_row_count_*`)
  — against a sample they measure the *sample* and report it as the dataset's
  size, so a healthy 5M-row file with `min_value: 4000000` would fail critically
  forever. Refused at author time in both directions (adding the check, and
  turning sampling on under one), and per check at run time for suites that
  predate the gate. Use a **volume monitor** instead: it counts the whole dataset
  without loading it.
- **Sampling on a comparison check's `source`** — the comparison reader
  materialises both sides in full for the diff, so the block would be ignored.

On Unity Catalog a seeded random sample is pushed down as
`TABLESAMPLE (p PERCENT) REPEATABLE (seed)`, so the seed genuinely pins the draw
rather than only being recorded.

**Every result says whether it was sampled**, and only when it genuinely was — a
sample larger than the dataset covered everything, and is reported as a complete
read. Within one run a volume monitor (pushed down, exact) and a sampled
expectation can sit side by side, so the label is per check, not per run.

## Author a check

1. Create (or open) a **suite** and point it at a **target** — a table (Snowflake/UC), a
   file/path or batch pattern (ADLS/S3), or an Iceberg `namespace.table`.
2. **Add check** opens a dedicated page (`/suites/<id>/checks/new`): pick a **category**,
   then the check type, then fill its config. The authoring paths:

### GX expectation (all datasources)

Pick a *Column values* / *Table shape* expectation (e.g. `Column values not null`), name
it, set the column, and optionally band severity with **Warn ≥ / Fail ≥ / Critical ≥**
thresholds over the unexpected-%. Leave thresholds blank for binary pass/fail.

### Custom SQL (Snowflake / Unity Catalog — ADR 0019)

A read-only SQL rule in the Monaco editor: **any rows returned are failures**. Use
`{batch}` as a placeholder for the suite's target table
(`SELECT * FROM {batch} WHERE amount < 0`). Single read-only statement enforced
server-side.

The query runs **in the warehouse**, so the result is a pass/fail plus the number
of rows returned — no severity banding (a row count isn't comparable across tables).

**On Unity Catalog the suite's run target must name a schema.** Custom SQL is the
one check kind that needs it: the query is addressed as `catalog.schema.table`, and
a two-part name would silently resolve against the session's default schema — a
*different table*, quietly checked. A UC target without a schema therefore errors
its custom-SQL checks (with that reason on the result) while every other check in
the suite runs normally. Set the schema on the suite's run target to fix it.
Snowflake is unaffected — its schema comes from the connection.

### Freshness monitor (all datasources — ADR 0012/0030)

*How stale is the target?* Point it at the load/updated **timestamp column**; the check
measures hours since `MAX(column)` and bands that age with the thresholds. A **fail or
critical threshold is required** — without one, a freshness check could never fail.

**On a flat file (ADLS Gen2 / S3) the timestamp column is optional.** Leave it blank
and the check measures **when the file last landed** (the object's modified time)
instead of the newest timestamp inside it. These catch different failures, and a
landing zone usually wants both:

| Blank column (arrival time) | Named column (in-file `MAX`) |
|---|---|
| Catches **"the producer stopped sending files"** — no new file has arrived. An in-file `MAX` is blind to this: the newest file is old, but its rows look perfectly fresh. | Catches **"files keep arriving but the data in them is stale"** — the pipeline runs, the content doesn't advance. |
| Costs a listing, no data read. | Reads the resolved batch. |

Caveats for the in-file form: a CSV's timestamps are text, so they're parsed — use
**ISO-8601**, since an ambiguous `06/07/2026` follows pandas' day-first inference.
A **numeric** column is refused outright rather than read as an epoch offset, which
would date your data to 1970 and fire critical staleness forever.

### Volume monitor (all datasources — ADR 0012/0030)

*Did the load deliver?* Set the expected **min/max row count**; thresholds optionally
band the % by which the count falls outside the range (a spike can exceed 100%), or
leave them blank for binary in-range pass/fail. On a flat file the count is over the
**resolved batch** — the single file the target's batch pattern selects, not the
whole prefix.

### Schema-drift monitor (all datasources — ADR 0012, #592)

*Did the shape change under you?* Capture a **baseline** column-name/type snapshot,
then each run diffs the live snapshot against it and flags any add / drop /
type-change. Introspection is per-datasource, never a `CheckRunner`/GX pass or a
data scan: `information_schema` for Snowflake/Unity Catalog, the Parquet footer (or
a bounded CSV header sample) for ADLS Gen2/S3 flat files, and the loaded table's own
metadata for Iceberg. Re-baseline explicitly once you've reviewed a drift and want
it as the new normal — it is never re-baselined for you.

### DQ dimension (ADR 0038)

Every check carries a **DQ dimension** — the quality aspect it measures. This is a
third axis, separate from the check *kind* (how it works) and the expectation type
(the specific rule):

| Dimension | Question it answers |
|---|---|
| Accuracy | Does the data match reality / a trusted source? |
| Completeness | Is all the expected data present? |
| Consistency | Do related datasets agree with each other? |
| Integrity | Do relationships between datasets hold? |
| Timeliness | Is the data recent enough? |
| Uniqueness | Are there unexpected duplicates? |
| Validity | Does the data conform to its rules and formats? |

**It is filled in for you.** The editor defaults it from the check type — a
not-null check is Completeness, a freshness monitor is Timeliness — and you can
change it at any time, including long after the check was created. Derivation is a
good guess about intent, not a fact: the same range check is *Validity* when it
bounds a percentage and *Accuracy* when it asserts a reconciled total.

Two dimensions are **never** guessed. **Accuracy** and **Integrity** can't be
inferred from the shape of a rule, and a **custom SQL** check is an arbitrary
predicate with no derivable answer at all — those start blank for you to set.

Leaving it blank is legitimate: the check is recorded as *unclassified* and shows
up as a **coverage gap** rather than being quietly filed under a dimension it
doesn't belong to. That matters because the point of dimensions is coverage —
"this table has no Timeliness checks at all" is the actionable finding, and it
would be a lie if unclassified checks were silently bucketed.

Checks created before this feature landed are unclassified until you next edit
them; they were deliberately not bulk-classified, so a derived guess is never
mistaken for someone's decision.

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
