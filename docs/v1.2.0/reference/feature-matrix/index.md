# Feature matrix

One-page reference: what runs where. For the readable tour of everything DataQ offers see
[Features](../guides/features.md); for the concepts behind the columns see [Concepts](../get-started/concepts.md) and
[Datasources & checks](../guides/datasources-checks.md).

## Check kinds × datasources

| Check kind | Snowflake | Unity Catalog | PostgreSQLᵖ | MySQL / MariaDBᵐ | Trinoᵗ | SQL Serverᵉ | Athenaᵒ | Redshiftʳ | ADLS Gen2 (files) | S3 (files)ˢ | Iceberg |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| GX expectations (column / table shape) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ (no regex) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Snowflake DMF (native metric functions)ᵈ | ✅ | — | — | — | — | — | — | — | — | — | — |
| Databricks DQX (runs in your workspace)ʲ | — | ✅ | — | — | — | — | — | — | — | — | — |
| Custom SQL (rows returned = failures)ᶜ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | — | — |
| Freshness monitor (hours since latest timestamp) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Freshness from **file arrival time** (no column — catches "no new file") | — | — | — | — | — | — | — | — | ✅ | ✅ | — |
| Volume monitor (row count in range) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Aggregate monitor (mean / median / sum / stdev / min / max in two-sided bands)ᵍ | ✅ | ✅ | ✅ | ✅ (no median) | ✅ (no median) | ✅ | ✅ (no median) | ✅ | ✅ | ✅ | ✅ |
| Anomaly monitor (z-score vs a learned baseline)ᵃ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | — | — |
| Schema-drift monitor (column add/drop/type-change vs a stored baseline)ᵇ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Comparison / reconciliation (diff vs a baseline connection) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Column profiler (nulls, distinct, min/max, top values) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Browse for the run target (catalog → schema → table / folders → file) | ✅ (schema → table) | ✅ | ✅ (schema → table) | ✅ (schema → table) | ✅ (schema → table) | ✅ (schema → table) | ✅ (database → table) | ✅ (schema → table) | ✅ | ✅ | ✅ (namespace → table) |
| DQ dimension on checks + asset scorecard (coverage + score) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Dry-run preview | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

ᵃ **Live-verified.** The anomaly monitor learns a rolling mean/stddev of the
target's own row count or freshness age (optionally per weekday) and bands each
run's z-score through the usual warn/fail/critical thresholds; below its
`min_points` of history it reports `skip`, never a fabricated pass. Both target
metrics were run against live Snowflake and live Unity Catalog tables via the real
`measure_metric` path — a tick here is earned by an **executed** run against a live
datasource, never by a passing unit test. The dashes are a real restriction, not an omission: the
anomaly executor takes its own measurement over a live SQL connection, while
Iceberg and flat files compute their monitor scalars natively inside their
runners, which stateful kinds never reach.

ᵍ **Aggregate monitor.** One statistic over one numeric column, recorded as the
result's metric, with nested two-sided warn / fail / critical bounds in its config. Median is
exact wherever it is offered; MySQL/MariaDB, Trino and Athena have only an approximate
percentile, so it is refused there at author time. Verified by executed runs, author → worker
→ `metric_value` and the dry-run: live Snowflake, live Unity Catalog, live Amazon Athena, live
Amazon Redshift Serverless and live Azure SQL (SQL Server), PostgreSQL, MySQL 8, MariaDB 11,
Trino, flat files on Azurite (CSV and Parquet) and Iceberg on a local SQL catalog.

ᵇ **Schema drift** (ADR 0012)
diffs a live column-name/type snapshot against a stored baseline and flags
add/drop/type-change. Unlike custom SQL it never goes through a `CheckRunner`/GX at
all, so it isn't gated to SQL datasources: introspection is per-datasource —
`information_schema` for Snowflake/Unity Catalog, the Parquet footer, a bounded
CSV header sample or a JSON file's first 1 MiB for ADLS/S3 flat files, and the loaded
table's own metadata for Iceberg (no data scan on any of them except the CSV and JSON
samples). Re-baseline explicitly
once a drift is expected and reviewed.

ᶜ **Unity Catalog custom SQL is supported since v1.1.** It runs against a GX
Databricks-SQL batch over the target table. Since v1.2, ordinary catalog
expectations run on that same SQL batch by default — the warehouse evaluates and the
worker never materialises the table; the DataFrame batch remains for
`expect_column_values_to_be_of_type`, suites with a declared sample, and the
`UC_SQL_PUSHDOWN=false` rollback. Verified against a live
Unity Catalog table, including the operational-`error` path when a column will not
resolve. Custom SQL stays binary pass/fail on both SQL datasources (ADR 0019 §4 — a
row count is not a bandable metric), so `metric_value` is null by design, not by
omission.

ᵈ **Snowflake DMF** (ADR [0036](../adr/0036-connection-anchored-check-engines.md)) is the
first platform-native check engine — an alternative `check.engine` for seven expectation
types (null count, null percent, duplicate count, unique count, blank count, future-timestamp
percent, accepted values) plus the freshness monitor, invoking Snowflake's
own `SNOWFLAKE.CORE.*` metric functions instead of a GX expectation. It also runs **custom
DMFs** (`dmf:custom`): a function the customer created, named `DATABASE.SCHEMA.FUNCTION` and
called over columns of the suite's table; connection tests list the ones the role can use for
the editor to suggest. Engine is selected per
check, on a Snowflake connection only; `kind` stays `expectation` either way. Testing or
re-authenticating a Snowflake connection also probes DMF availability (Enterprise Edition +
grant) and stores the result on the connection — surfaced on the connection list and, as a caveat
rather than a hard gate, in the check editor's engine picker. The probe `EXPLAIN`s a system
DMF call over one table the connection's role can already see: Snowflake compiles it and checks
the function and the role's privileges, but executes nothing and reads no rows. If the role can
see no table, or the probe fails for a reason unrelated to DMFs, the connection shows "couldn't
determine" rather than "unavailable".

ʲ **Databricks DQX** (ADR [0036](../adr/0036-connection-anchored-check-engines.md)) is the
second platform-native engine: eight row rules evaluated by Databricks Labs DQX in a
serverless job in the connection's own workspace, one job per run. DataQ never ships DQX,
whose licence permits use only with Databricks services. The job returns failing-row counts
only. A check in **stream** mode evaluates only the rows appended since its last run, so it
works on Lakeflow streaming tables and append-only Delta tables. See
[Databricks DQX](../guides/datasources-checks.md#databricks-dqx-adr-0036).

ᵖ **PostgreSQL** — any server, self-hosted or managed; everything runs by pushdown
on read-only sessions. Every row was verified by an **executed** run against a real
PostgreSQL 16 through the connection's own least-privileged role, and the suite's CI
re-runs that battery (every SQL-capable expectation type, custom SQL, freshness over
`timestamptz`/`timestamp`/`date`, volume, anomaly measurement, profiler, schema drift,
comparison reads,
inventory enumeration and browsing) against the test database on every change. The
profiler reports min/max as unavailable for types PostgreSQL cannot order (`boolean`,
`jsonb`, …) — see [Datasources & checks](../guides/datasources-checks.md#postgresql).

ᵐ **MySQL / MariaDB** — any server, on the same generic SQL base as PostgreSQL, through
the MIT-licensed PyMySQL driver. The same battery was **executed** against MySQL 8.4,
MariaDB 11.8 and MariaDB 10.6, each through its own least-privileged user. Two engine facts to know: GX's
MySQL uniqueness check builds session temporary tables, so it runs on its own session
without the read-only guard and needs the `CREATE TEMPORARY TABLES` grant; and
uniqueness, set membership and comparisons follow the column's collation (`'a'` and
`'A'` are duplicates under the default case-insensitive one) — see
[Datasources & checks](../guides/datasources-checks.md#mysql-mariadb).

ᵒ **Amazon Athena** — serverless SQL over S3 through the Glue catalog, one data catalog per
connection in one region. **Live-verified** against a real Athena workgroup as a
least-privileged IAM user: every SQL-batch expectation type, custom SQL, the freshness,
volume and anomaly monitors, the profiler, schema drift, comparison reads, inventory,
browsing and an end-to-end persisted run. Two engine facts: **every check is a billed
query** (Athena charges per byte scanned, with a per-query minimum), and there is **no
read-only session** — the IAM policy is the guarantee (a write by the reader was refused).

ʳ **Amazon Redshift** — provisioned clusters and Serverless workgroups. **Live-verified**
against a Redshift Serverless workgroup as a user granted only `USAGE` and `SELECT` on one
schema: every SQL-batch expectation type, custom SQL (including over `SUPER` paths), the
freshness (`TIMESTAMP`, `TIMESTAMPTZ`, `DATE`), volume and anomaly monitors, the profiler over a
table and a late-binding view, schema drift, comparison reads, inventory, browsing and an
end-to-end persisted run. Sessions are read-only at the server (a write by the reader was
refused); a materialized view is listed as a view.

ᵗ **Trino** — any cluster (incl. Starburst), one catalog per connection, and every store that
catalog federates; everything runs by pushdown on the cluster. The same battery was
**executed** against Trino 483 (the `memory` connector, and a mixed-case PostgreSQL table
through the `postgresql` connector), plus HTTPS with a private CA, password and JWT
authentication and file-based access control. Two engine facts: Trino has **no read-only
session**, so the read-only guarantee is the Trino user's own access control; and every name is
**lower case** — see [Datasources & checks](../guides/datasources-checks.md#trino).

ᵉ **SQL Server** — SQL Server, Azure SQL Database, Synapse and (through the optional,
user-installed ODBC lane) Microsoft Fabric SQL endpoints; SQL login or Entra service principal;
TLS always verified; everything by pushdown. Like Trino and unlike PostgreSQL and MySQL, sessions are **not**
read-only at the server (TDS has no such setting) — the login must be a `db_datareader`. Every
ticked row was verified by an **executed** run against a live Azure SQL database with both auth
modes (every SQL-capable expectation type, custom SQL, freshness over
`datetimeoffset`/`datetime2`/`date`, volume, anomaly measurement, profiler, schema drift,
comparison reads, inventory enumeration, browsing and a persisted suite run), on both the
python-tds and the ODBC lane, plus a Fabric Warehouse and Lakehouse SQL endpoint on the ODBC
lane (where seven multi-column / uniqueness types are refused — GX builds a temporary table
Fabric does not support). That battery is
an opt-in live lane (`tests/integration/test_mssql_live.py`), not CI — no SQL Server can run in
CI without a commercial licence. The four regex expectations are refused on SQL Server (no T-SQL
translation in GX), and Fabric SQL endpoints need the user-installed ODBC lane — see
[Datasources & checks](../guides/datasources-checks.md#sql-server-azure-sql-fabric-t-sql).

ˢ **S3 means AWS S3 *and* any S3-compatible store** — MinIO, Ceph/RadosGW, Cloudflare R2,
Wasabi, Backblaze B2, SeaweedFS or an on-prem gateway. Set the connection's optional
endpoint URL; every row in this column applies identically either way. See
[Datasources & checks](../guides/datasources-checks.md#s3-compatible-object-stores).

Custom SQL runs a SQL query, so it's **SQL-datasource only** (Snowflake, Unity Catalog, PostgreSQL,
MySQL/MariaDB, Trino, SQL Server;
there is no flat-file support, and no issue currently tracks adding it — flat files get freshness/volume monitors instead (see the rows above);
Iceberg is not SQL-queryable — reads go through `pyiceberg` scans, not a query engine).
**Comparison checks** (ADR [0015](../adr/0015-two-connection-comparison-check-model.md))
diff the suite's dataset (the **target under test**) against a baseline on any other
datasource connection — cross-type and cross-env both supported — joined on key columns,
producing matched / mismatched / additional-per-side buckets with a mismatch-% metric,
capped fail-fast reads (`COMPARISON_MAX_ROWS`), redacted samples, and an on-demand
CSV/XLSX report download (derived, never stored). Either SQL side may use a read-only
query projection.

The freshness/volume monitors run on **every datasource** — the SQL datasources, Apache
Iceberg (computed natively via `pyiceberg` scans, not SQL; ADR 0012/0030), and ADLS Gen2 /
S3 flat files (over the resolved batch). On a flat file, a freshness monitor with **no
timestamp column** measures the object's arrival time instead — catching a producer that
stopped sending files, which a timestamp inside the data cannot see. Flat-file suites target a file or a batch pattern (e.g.
`orders_*.csv`) in CSV, Parquet or JSON (JSON Lines / an array of flat objects; nested JSON is
refused); Iceberg suites target a `namespace.table`. Dry-run
preview works on every datasource with a runner — Snowflake, Unity Catalog, flat files,
and Iceberg.

## Assets & lineage × datasources

Every datasource gets a first-class **asset** (identity = the OpenLineage dataset naming
spec, ADR 0034); lineage edges are **observed, never inferred**, and can arrive through
five mechanisms:

1. **Run-stamping** — every suite run (and suite save) resolves its target to an asset row
   and stamps `last_seen`. Works on all datasources; unreferenced stale rows are retired by
   the daily orphan sweep.
2. **dbt `manifest.json`** — table-level model lineage cached into `lineage_edges` on every
   successful dbt build ([details](../guides/orchestration.md#lineage-from-manifestjson-adr-0034)).
   dbt models warehouse tables, so raw flat files don't appear here.
3. **OpenLineage emission** (outbound) — DataQ broadcasts RunEvents + DQ facets per run to
   any OL-compatible receiver (`OPENLINEAGE_URL`, dark by default).
4. **Catalog pull** — the `LineageProvider` seam pulls a governance catalog's graph back in
   as `source='marquez'` edges (daily beat, dark by default;
   [details](../guides/orchestration.md#lineage-from-a-catalog-the-lineageprovider-seam-adr-0034)).
5. **Warehouse-native pull** — the `WarehouseLineageProvider` seam reads the
   warehouse's OWN lineage views straight into `lineage_edges` with `source='snowflake'` /
   `'unity_catalog'`: Snowflake `OBJECT_DEPENDENCIES` (all editions) → `ACCESS_HISTORY` /
   `GET_LINEAGE` (Enterprise); Unity Catalog `system.access.table_lineage`. First-hand, no
   dbt hop. Daily beat, **on by default** (`WAREHOUSE_LINEAGE_ENABLED=false` opts out — a
   principal missing the grants degrades per connection); the tier that answered and any
   degraded/failing state surface on the asset's
   lineage graph so a view-level-only or stale graph never reads as a confident complete
   one ([details](../guides/orchestration.md#lineage-from-the-warehouse-the-warehouselineageprovider-seam)).
   **Column grain:** where the warehouse offers it (UC
   `system.access.column_lineage` — live-verified), the pull refines each table edge with
   `upstream column → downstream column` pairs, shown on the asset page to every
   workspace member (ADR 0037 — column names are schema metadata, i.e. identity), and any one
   column can be **traced** to its origin or its downstream columns. An edge without pairs
   always says why (`column_coverage`: none recorded · unavailable · unknown · not captured).
   Snowflake's column grain comes from `ACCESS_HISTORY` on every tier (`GET_LINEAGE` rows are
   table-grain) and reports honestly unavailable where the grant is missing. **Snowpark scratch is
   stitched, not dropped:** a
   pipeline that materializes through `SNOWPARK_TEMP_*` yields the real `A → B` edge, with the
   scratch object never materialized as an asset.

| Datasource | Asset entity | ① Run-stamping | ② dbt manifest | ③ OL emission | ④ Catalog pull | ⑤ Warehouse-native |
|---|---|:-:|:-:|:-:|:-:|:-:|
| Snowflake | `snowflake://{org}-{account}` / `DB.SCHEMA.TABLE` | ✅ | ✅ (live-verified) | ✅ | ✅ | ✅ (OBJECT_DEPENDENCIES live; ACCESS_HISTORY + **GET_LINEAGE per-seed traversal** Enterprise, built on a live prod-Enterprise capture; **+ column grain from ACCESS_HISTORY** on both tiers, live-verified; **+ view and dynamic-table column lineage** via `GET_LINEAGE`'s column domain) |
| Unity Catalog | `unitycatalog://{host}` / `catalog.schema.table` | ✅ | ✅ (adapter-aware) | ✅ | ✅ | ✅ (system.access.table_lineage, incremental; **+ column grain**) |
| ADLS Gen2 (files) | `abfss://{container}@{account}.dfs.core.windows.net` (a sovereign cloud or another ADLS-compatible endpoint keeps its own DFS host: `abfss://{container}@{account}.dfs.core.chinacloudapi.cn`, `abfss://{workspace}@onelake.dfs.fabric.microsoft.com`; a path-style emulator endpoint such as Azurite's is named after its own host and the account in its path: `abfss://{container}@{host}:{port}/{account}`) / pattern **base prefix** | ✅ | — | ✅ | ✅ | — |
| S3 (files) | `s3://{bucket}` / base prefix | ✅ | — | ✅ | ✅ | — |
| Iceberg | `{catalog_uri}` / `namespace.table` | ✅ | —¹ | ✅ | ✅ | —³ |
| MySQL / MariaDB | `mysql://{host}:{port}` / `database.table` | ✅ (+ inventory sync) | ✅ (dbt-mysql naming) | ✅ | ✅ | —⁴ |
| Trino | `trino://{host}:{port}` / `catalog.schema.table` | ✅ (+ inventory sync) | ✅ (dbt-trino naming) | ✅ | ✅ | —⁴ |
| PostgreSQL | `postgres://{host}:{port}` / `database.schema.table` | ✅ (+ inventory sync) | ✅ (dbt-postgres naming) | ✅ | ✅ | —⁴ |
| SQL Server | `mssql://{host}:{port}` / `database.schema.table` | ✅ (+ inventory sync) | ✅ where dbt-sqlserver / dbt-fabric emit `database.schema.table` names | ✅ | ✅ | —⁴ |
| BI reports / dashboards | not yet materialized² | — | — | — | reserved² | — |

¹ dbt-managed Iceberg tables surface through the warehouse adapter (Snowflake/UC rows);
native `pyiceberg` connections have no dbt slice of their own.
³ Warehouse-native lineage reads a query engine's lineage view; a native `pyiceberg`
connection has no engine to ask (an engine-registered Iceberg table is covered under its
Snowflake/UC connection).
⁴ PostgreSQL, MySQL, Trino and SQL Server keep no lineage log DataQ reads, so there is no warehouse-native pull; the
inventory sync still enumerates its tables (ADR 0040).
² The lineage graph's node-kind contract reserves `bi_report`/`dashboard` — a BI node
(e.g. a Power BI report downstream of a mart) becomes representable the moment a capable
catalog (Purview/DataHub) lands behind the seam plus an `assets.kind` column; no schema or
query rewrite needed.

Orchestration providers contribute **no lineage of their own** — ADF and Airflow are
observed for pipeline runs only; dbt is the one orchestration provider that doubles as a
lineage source (mechanism ②). Flat-file and Iceberg edges therefore depend on an external
catalog knowing about them (mechanism ④).

## Ways a suite runs

| Mode | Where | Notes |
|---|---|---|
| Run now | Suite detail → Run panel | Live per-check progress + cancel |
| Cron schedule | Suite detail → Schedules | 5-field cron, IANA timezone, DST-aware, [no backfill](../guides/scheduling.md) |
| Pipeline trigger | Suite detail → Triggers | Runs on a pipeline/DAG/dbt-job **success** — ADF + Airflow + dbt, see [Orchestration](../guides/orchestration.md) |
| API / MCP | `POST /suites/{id}/run` · `trigger_suite_run` MCP tool | Same authz as the UI |

## Severity & results

| Capability | Notes |
|---|---|
| Severity tiers | warn / fail / critical, banded from the observed unexpected-% (ADR 0005/0016) |
| Operational statuses | `error` (evaluation threw) and `skip` (precondition unmet) are distinct from failures |
| Health score | Severity-weighted, on the Dashboard |
| Failing-row samples | Redacted column-aware before display (suite column policy + classifier) |
| Run history retention | Samples purged after the retention window; metric trends kept |

## Alerting

| Capability | Notes |
|---|---|
| Channels | Teams, Slack, email — each workspace default or per-suite override; reusable channels (incl. a generic webhook type) also exist, API-only currently — [details](../guides/notifications.md) |
| Threshold | Per suite: fail-only / warn+ (default) / always |
| Routing | Severity-aware urgency; critical escalates |
| Dedup | First failure / escalation only; clean run resets |
| Snooze | Per check, N hours |

## Orchestration providers (not datasources)

| Provider | Failure detection | Trigger on success |
|---|---|---|
| Azure Data Factory | Azure Monitor alert → webhook (+10-min poll) | ✅ trigger bindings |
| Apache Airflow | DAG callback → HMAC webhook (+10-min poll) | ✅ trigger bindings |
| dbt | Post-build callback → HMAC webhook (+10-min `run_results.json` artifact poll) | ✅ trigger bindings |

### Managed Airflow distributions

The Airflow provider talks to the stock **Airflow REST API** and is not coupled to any
particular host, so managed distributions work through the same connection type. Set
`base_url` to the deployment's Airflow endpoint and supply the platform's API token as the
credential (`auth_type: token`, the default — sent as `Authorization: Bearer …`).

| Distribution | Expected to work | Status |
|---|---|---|
| Self-hosted / OSS Airflow | ✅ | **Verified** against a self-hosted Airflow |
| Astronomer (Astro) | ✅ via an Astro **Deployment API token** as the Bearer credential, with `base_url` set to the deployment's Airflow URL (`https://<org>.astronomer.run/<deployment-id>`) | **Untested** — no Astro deployment has been exercised; compatible by construction, not by observation |
| MWAA / Cloud Composer | Likely, same Bearer shape | **Untested** |

The DAG-callback snippet in [`integrations/airflow/`](../../integrations/airflow/) is
host-agnostic — it POSTs an HMAC-signed event to DataQ and needs only outbound network
access from the worker, so it applies unchanged on a managed deployment.

**If you run DataQ against a managed distribution, please report back** — the honest status
above is "should work", and only a real deployment can upgrade that to "does".

## Access — workspace roles × capabilities

Two orthogonal axes (ADR [0033](../adr/0033-workspace-roles-rbac.md)). Your **workspace
role** says what kind of user you are; **per-suite grants** (`view` / `edit`) say what you
may touch. Neither replaces the other — a Member with no share on a suite still cannot see
it. Both are enforced server-side on REST **and** MCP, and both resolve **per request**, so
a role change takes effect on the target's next call, including calls made with API tokens
they already hold.

| Capability | Admin | Member | Viewer |
|---|---|---|---|
| See/use suites shared to them | ✅ | ✅ | ✅ (view only) |
| Create/import suites (become owner) | ✅ | ✅ | ❌ |
| Receive `edit` shares | ✅ | ✅ | ❌ — capped at `view` |
| Connections: create / edit / delete / re-auth | ✅ | ❌ | ❌ |
| Connections: list & reference in suites | ✅ | ✅ | list only |
| Connections: test (saved) | ✅ | ✅ | ❌ |
| Connections: test (unsaved draft) | ✅ | ❌ | ❌ |
| Mint API tokens (inherit the user's access) | ✅ | ✅ | ✅ |
| `/admin`, implicit suite-admin, workspace-wide visibility | ✅ | ❌ | ❌ |
| Manage roles in-app | ✅ | ❌ | ❌ |

Roles are managed in **Admin → Members**. `WORKSPACE_ADMIN_EMAILS` remains a **bootstrap
seed and lockout break-glass**: it grants Admin but never removes it, and a role change must
always leave at least one *stored-role* admin. See [security](../security/overview.md) for the residual
risk that carries.

## Interfaces

| Surface | What |
|---|---|
| Web UI | Dashboard · Assets · Connections · Suites · Results · Profile · Admin · Settings (Assets lead as the primary lens — ADR 0034 nav inversion; the Dashboard opens with an asset-health strip, and suites/runs link back to their asset) |
| REST API | Versioned `/api/v1` (Swagger in non-prod) |
| MCP | 54 curated tools at `/mcp` for AI assistants (ADR 0008). Served in both auth modes — SSO and email OTP; under OTP the credential is a **PAT only** ([MCP setup](../guides/mcp-setup.md)) |
